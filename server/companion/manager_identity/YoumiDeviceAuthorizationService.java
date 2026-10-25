package xiaozhi.modules.device.service.impl;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import org.springframework.context.annotation.Lazy;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import jakarta.annotation.Resource;
import lombok.RequiredArgsConstructor;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.common.user.UserDetail;
import xiaozhi.modules.agent.dao.AgentDao;
import xiaozhi.modules.agent.entity.AgentEntity;
import xiaozhi.modules.agent.service.AgentService;
import xiaozhi.modules.security.user.SecurityUser;
import xiaozhi.modules.sys.dao.SysUserDao;
import xiaozhi.modules.sys.entity.SysUserEntity;

/** Private device grants; existing manager accounts and device binding remain authoritative. */
@Service
@RequiredArgsConstructor
public class YoumiDeviceAuthorizationService {
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final long CREDENTIAL_SECONDS = 30L * 86400;
    private final JdbcTemplate db;
    private final AgentDao agents;
    private final SysUserDao users;
    private final RedisUtils redis;
    @Lazy @Resource private AgentService agentService;

    private record Binding(long operator, long owner, String agent) {}

    private Binding binding(String deviceId, boolean lock) {
        List<Map<String, Object>> rows = db.queryForList(
                "SELECT user_id,agent_id FROM ai_device WHERE id=?" + (lock ? " FOR UPDATE" : ""), deviceId);
        if (rows.isEmpty() || !(rows.getFirst().get("user_id") instanceof Number userId)) return null;
        if (!(rows.getFirst().get("agent_id") instanceof String agentId)) return null;
        AgentEntity agent = agents.selectById(agentId);
        if (agent == null || agent.getUserId() == null) return null;
        return new Binding(userId.longValue(), agent.getUserId(), agent.getId());
    }

    private Binding requireOwner(String deviceId) {
        Binding current = binding(deviceId, true);
        UserDetail user = SecurityUser.getUser();
        if (current == null || user == null || user.getId() == null || user.getId() != current.operator()
                || !agentService.checkAgentPermission(current.agent(), user.getId())) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        return current;
    }

    private void ensureRow(String deviceId) {
        db.update("INSERT IGNORE INTO ai_mia_device_authorization(device_id) VALUES (?)", deviceId);
    }

    private Map<String, Object> row(String deviceId, boolean lock) {
        List<Map<String, Object>> rows = db.query(
                "SELECT * FROM ai_mia_device_authorization WHERE device_id=?" + (lock ? " FOR UPDATE" : ""),
                (rs, index) -> {
                    Map<String, Object> result = new HashMap<>();
                    for (int column = 1; column <= rs.getMetaData().getColumnCount(); column++) {
                        result.put(rs.getMetaData().getColumnLabel(column), rs.getObject(column));
                    }
                    result.put("code_expires_at", rs.getTimestamp("code_expires_at"));
                    result.put("credential_expires_at", rs.getTimestamp("credential_expires_at"));
                    return result;
                }, deviceId);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private static long number(Map<String, Object> row, String name) {
        return ((Number) row.get(name)).longValue();
    }

    private static boolean sameBinding(Map<String, Object> row, Binding current) {
        return row != null && current != null && row.get("operator_id") instanceof Number
                && row.get("owner_id") instanceof Number
                && number(row, "operator_id") == current.operator() && number(row, "owner_id") == current.owner()
                && Objects.equals(row.get("agent_id"), current.agent());
    }

    private boolean currentAuthority(Binding current) {
        if (current == null) return false;
        SysUserEntity operator = users.selectById(current.operator());
        SysUserEntity owner = users.selectById(current.owner());
        return operator != null && owner != null && Integer.valueOf(1).equals(operator.getStatus())
                && Integer.valueOf(1).equals(owner.getStatus())
                && (current.operator() == current.owner() || Integer.valueOf(1).equals(operator.getSuperAdmin()));
    }

    private static String random(int bytes) {
        byte[] value = new byte[bytes];
        RANDOM.nextBytes(value);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    private static String digest(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private static boolean matches(Object hash, String supplied) {
        return hash instanceof String && MessageDigest.isEqual(((String) hash).getBytes(StandardCharsets.US_ASCII),
                digest(supplied).getBytes(StandardCharsets.US_ASCII));
    }

    private static boolean future(Object value) {
        return value instanceof Timestamp timestamp && timestamp.toInstant().isAfter(Instant.now());
    }

    private void rateLimit(String source) {
        long minute = Instant.now().getEpochSecond() / 60;
        // Fixed window key plus expiry keeps the global and per-source storage bounded.
        if (redis.increment("mia:private-auth:global:" + minute, 90) > 120
                || redis.increment("mia:private-auth:source:" + minute + ":" + digest(source), 90) > 20) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
    }

    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> createCode(String deviceId) {
        return prepareCode(deviceId, random(16));
    }

    /** Independent virtual device, with the same grant lifecycle as physical clients. */
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> createVirtual(String agentId, String botUin) {
        UserDetail user = SecurityUser.getUser();
        if (user == null || user.getId() == null || !agentService.checkAgentPermission(agentId, user.getId())
                || !botUin.matches("[1-9][0-9]{4,19}")) throw new RenException(ErrorCode.NO_PERMISSION);
        AgentEntity agent = agents.selectById(agentId);
        if (agent == null || agent.getUserId() == null
                || !currentAuthority(new Binding(user.getId(), agent.getUserId(), agentId))) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        String device = "astrbot-" + random(18); // 32 chars, separate from every iOS/hardware ID.
        db.update("INSERT INTO ai_device(id,mac_address,user_id,agent_id,board,alias,auto_update,creator,create_date) "
                + "VALUES (?,?,?,?,?,'AstrBot QQ',0,?,NOW())",
                device, device, user.getId(), agentId, "youmi-astrbot", user.getId());
        return Map.of("device_id", device, "client_id", "astrbot:" + botUin,
                "authorization", createCode(device));
    }

    public void requireVirtual(Map<String, Object> scope) {
        if (!(scope.get("client_id") instanceof String client) || !client.matches("astrbot:[1-9][0-9]{4,19}")
                || db.queryForObject("SELECT COUNT(*) FROM ai_device WHERE id=? AND board='youmi-astrbot'",
                        Integer.class, scope.get("device_id")) != 1) throw new RenException(ErrorCode.NO_PERMISSION);
    }

    // Six digits identify a pending device request; they are never a bearer proof.
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> approveHardwareCode(String deviceId, String code) {
        if (code != null && code.matches("[a-f0-9]{32}")) return prepareCode(deviceId, code); // 1.0.2 compatibility
        if (code == null || !code.matches("[0-9]{6}")) throw new RenException(ErrorCode.NO_PERMISSION);
        Binding current = requireOwner(deviceId);
        Map<String, Object> pending = hardwarePending(deviceId);
        if (!currentAuthority(current) || !pendingCurrent(pending, current, row(deviceId, true))
                || !code.equals(pending.get("code"))) throw new RenException(ErrorCode.NO_PERMISSION);
        Map<String, Object> result = prepareCode(deviceId, code, (String) pending.get("hash"));
        pending.put("approved", true);
        pending.put("authority", hardwareAuthority(current, row(deviceId, true)));
        pending.put("expires", Instant.now().getEpochSecond() + 600);
        redis.set(hardwareKey(deviceId), pending, 600);
        return result;
    }

    private Map<String, Object> prepareCode(String deviceId, String code) {
        return prepareCode(deviceId, code, digest(code));
    }

    private Map<String, Object> prepareCode(String deviceId, String code, String codeHash) {
        Binding current = requireOwner(deviceId);
        if (!currentAuthority(current)) throw new RenException(ErrorCode.NO_PERMISSION);
        ensureRow(deviceId);
        Map<String, Object> grant = row(deviceId, true);
        if (!sameBinding(grant, current)) {
            db.update("UPDATE ai_mia_device_authorization SET credential_hash=NULL,credential_expires_at=NULL,client_id=NULL,"
                    + "revoked=1,credential_version=credential_version+1 WHERE device_id=?", deviceId);
        }
        db.update("UPDATE ai_mia_device_authorization SET operator_id=?,owner_id=?,agent_id=?,persona_id='mia',"
                + "authorization_code_hash=?,code_expires_at=?,wrong_attempts=0 WHERE device_id=?",
                current.operator(), current.owner(), current.agent(), codeHash,
                Timestamp.from(Instant.now().plusSeconds(600)), deviceId);
        return Map.of("authorization_code", code, "expires_in", 600, "device_id", deviceId,
                "binding_version", number(grant, "binding_version"));
    }

    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> exchange(String deviceId, String clientId, String code, String source) {
        rateLimit(source);
        if (code.startsWith("hardware:")) return null; // Only the pending-request path may redeem this namespace.
        Binding current = binding(deviceId, true);
        Map<String, Object> grant = row(deviceId, true);
        if (!sameBinding(grant, current) || !currentAuthority(current)) return null;
        if (number(grant, "wrong_attempts") >= 5 || !future(grant.get("code_expires_at"))) return null;
        if (!matches(grant.get("authorization_code_hash"), code)) {
            db.update("UPDATE ai_mia_device_authorization SET wrong_attempts=wrong_attempts+1 WHERE device_id=?", deviceId);
            // Returning denial commits the attempt; controller raises after the transaction.
            return null;
        }
        return issue(deviceId, clientId);
    }

    private static String hardwareKey(String deviceId) {
        return "mia:private-auth:hardware:" + digest(deviceId);
    }

    private static String hardwareAuthority(Binding binding, Map<String, Object> grant) {
        return digest(binding.operator() + ":" + binding.owner() + ":" + binding.agent() + ":"
                + (grant == null ? 0 : number(grant, "binding_version")) + ":"
                + (grant == null ? 0 : number(grant, "credential_version")));
    }

    private Map<String, Object> hardwarePending(String deviceId) {
        Object value = redis.get(hardwareKey(deviceId));
        if (!(value instanceof Map<?, ?> record)) return null;
        Map<String, Object> result = new HashMap<>();
        record.forEach((key, item) -> { if (key instanceof String text) result.put(text, item); });
        return result;
    }

    private static boolean pendingCurrent(Map<String, Object> pending, Binding binding, Map<String, Object> grant) {
        return pending != null && binding != null && pending.get("expires") instanceof Number expires
                && expires.longValue() > Instant.now().getEpochSecond()
                && hardwareAuthority(binding, grant).equals(pending.get("authority"));
    }

    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> pollHardware(String deviceId, String clientId, String proof, String source) {
        rateLimit(source);
        if (!proof.matches("[a-f0-9]{32}")) return null;
        Binding current = binding(deviceId, true);
        if (!currentAuthority(current)) return null;
        Map<String, Object> grant = row(deviceId, true);
        Map<String, Object> pending = hardwarePending(deviceId);
        String hash = digest("hardware:" + proof);
        if (!pendingCurrent(pending, current, grant)) {
            pending = new HashMap<>(Map.of("code", String.format(java.util.Locale.ROOT, "%06d", RANDOM.nextInt(1000000)),
                    "hash", hash, "client", clientId, "authority", hardwareAuthority(current, grant),
                    "expires", Instant.now().getEpochSecond() + 600, "approved", false));
            // The device row lock serializes this single, expiring pending request.
            redis.set(hardwareKey(deviceId), pending, 600);
        } else if (!hash.equals(pending.get("hash")) || !clientId.equals(pending.get("client"))) {
            return null;
        }
        if (Boolean.TRUE.equals(pending.get("approved")) && sameBinding(grant, current)
                && number(grant, "wrong_attempts") < 5 && future(grant.get("code_expires_at"))
                && matches(grant.get("authorization_code_hash"), "hardware:" + proof)) {
            Map<String, Object> result = issue(deviceId, clientId);
            redis.delete(hardwareKey(deviceId));
            return result;
        }
        return Map.of("pending_code", pending.get("code"), "device_id", deviceId, "client_id", clientId);
    }

    private Map<String, Object> issue(String deviceId, String clientId) {
        String credential = random(32);
        db.update("UPDATE ai_mia_device_authorization SET authorization_code_hash=NULL,code_expires_at=NULL,"
                + "credential_hash=?,credential_expires_at=?,client_id=?,revoked=0,credential_version=credential_version+1 "
                + "WHERE device_id=?", digest(credential), Timestamp.from(Instant.now().plusSeconds(CREDENTIAL_SECONDS)),
                clientId, deviceId);
        Map<String, Object> grant = row(deviceId, false);
        return Map.of("private_credential", credential, "expires_in", CREDENTIAL_SECONDS, "scope", scope(grant));
    }

    private Map<String, Object> scope(Map<String, Object> grant) {
        Map<String, Object> result = new HashMap<>(Map.of("owner", "user:" + number(grant, "owner_id") + ":agent:" + grant.get("agent_id"),
                "operator_id", number(grant, "operator_id"), "user_id", number(grant, "owner_id"),
                "agent_id", grant.get("agent_id"), "persona_id", grant.get("persona_id"),
                "device_id", grant.get("device_id"), "client_id", grant.get("client_id"),
                "binding_version", number(grant, "binding_version"),
                "credential_version", number(grant, "credential_version"),
                "expires_at", ((Timestamp) grant.get("credential_expires_at")).toInstant().toString()));
        result.put("permissions", List.of("memory:read", "memory:write", "memory:manage"));
        return result;
    }

    private boolean active(Map<String, Object> grant, Binding current, String clientId) {
        Object revoked = grant == null ? null : grant.get("revoked");
        boolean isRevoked = revoked == null || Boolean.TRUE.equals(revoked)
                || (revoked instanceof Number && ((Number) revoked).intValue() != 0);
        return sameBinding(grant, current) && currentAuthority(current) && !isRevoked
                && Objects.equals(grant.get("client_id"), clientId)
                && future(grant.get("credential_expires_at")) && grant.get("credential_hash") instanceof String;
    }

    private boolean valid(Map<String, Object> grant, Binding current, String clientId, String credential) {
        return credential != null && credential.matches("[A-Za-z0-9_-]{43}")
                && active(grant, current, clientId) && matches(grant.get("credential_hash"), credential);
    }

    @Transactional(readOnly = true)
    public Map<String, Object> verify(String deviceId, String clientId, String credential) {
        Binding current = binding(deviceId, false);
        Map<String, Object> grant = row(deviceId, false);
        return valid(grant, current, clientId, credential) ? scope(grant) : null;
    }

    // ConfigController holds this transaction through the private config lookup.
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> requirePrivateConfiguration(String deviceId, String clientId, String credential) {
        Binding current = binding(deviceId, true);
        Map<String, Object> grant = row(deviceId, true);
        if (!valid(grant, current, clientId, credential)) throw new RenException(ErrorCode.NO_PERMISSION);
        return scope(grant);
    }

    @Transactional(readOnly = true)
    public Map<String, Object> checkScope(String deviceId, String clientId, String expectedOwner,
                                          long operatorId, long bindingVersion, long credentialVersion) {
        Binding current = binding(deviceId, false);
        Map<String, Object> grant = row(deviceId, false);
        if (!active(grant, current, clientId) || number(grant, "operator_id") != operatorId
                || number(grant, "binding_version") != bindingVersion
                || number(grant, "credential_version") != credentialVersion) return null;
        Map<String, Object> actual = scope(grant);
        return expectedOwner.equals(actual.get("owner")) ? actual : null;
    }

    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> renew(String deviceId, String clientId, String credential, String source) {
        rateLimit(source);
        Binding current = binding(deviceId, true);
        Map<String, Object> grant = row(deviceId, true);
        return valid(grant, current, clientId, credential) ? issue(deviceId, clientId) : null;
    }

    // Server-owned queued work carries versions, never a reusable credential.
    public Map<String, Object> requireSnapshot(Map<String, Object> snapshot, boolean lock) {
        if (snapshot == null || !snapshot.keySet().equals(java.util.Set.of("owner", "operator_id",
                "device_id", "client_id", "binding_version", "credential_version"))) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        String device = snapshotText(snapshot, "device_id", 32);
        String client = snapshotText(snapshot, "client_id", 128);
        String owner = snapshotText(snapshot, "owner", 256);
        Binding current = binding(device, lock);
        Map<String, Object> grant = row(device, lock);
        if (!active(grant, current, client)
                || number(grant, "operator_id") != snapshotNumber(snapshot, "operator_id")
                || number(grant, "binding_version") != snapshotNumber(snapshot, "binding_version")
                || number(grant, "credential_version") != snapshotNumber(snapshot, "credential_version")) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        Map<String, Object> actual = scope(grant);
        if (!owner.equals(actual.get("owner"))) throw new RenException(ErrorCode.NO_PERMISSION);
        return actual;
    }

    private String snapshotText(Map<String, Object> snapshot, String key, int maximum) {
        Object value = snapshot.get(key);
        if (!(value instanceof String text) || text.isBlank() || text.length() > maximum
                || !text.matches("[A-Za-z0-9_:.\\-]+")) throw new RenException(ErrorCode.NO_PERMISSION);
        return text;
    }

    private long snapshotNumber(Map<String, Object> snapshot, String key) {
        Object value = snapshot.get(key);
        if (!(value instanceof String || value instanceof Integer || value instanceof Long)) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        String text = value.toString();
        if (!text.matches("0|[1-9][0-9]{0,18}")) throw new RenException(ErrorCode.NO_PERMISSION);
        try {
            return Long.parseLong(text);
        } catch (NumberFormatException invalid) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
    }

    @Transactional(rollbackFor = Exception.class)
    public void revoke(String deviceId) {
        requireOwner(deviceId);
        ensureRow(deviceId);
        invalidate("device_id=?", deviceId, false);
    }

    private void invalidate(String condition, Object value, boolean bindingChange) {
        // Conditions are fixed internal call sites, never network input.
        db.update("UPDATE ai_mia_device_authorization SET revoked=1,authorization_code_hash=NULL,code_expires_at=NULL,"
                + "credential_hash=NULL,credential_expires_at=NULL,client_id=NULL,credential_version=credential_version+1"
                + (bindingChange ? ",binding_version=binding_version+1" : "") + " WHERE " + condition, value);
    }

    @Transactional(rollbackFor = Exception.class)
    public void bindingChanged(String deviceId) {
        ensureRow(deviceId);
        invalidate("device_id=?", deviceId, true);
    }

    @Transactional(rollbackFor = Exception.class)
    public void agentDeleted(String agentId) {
        invalidate("agent_id=?", agentId, true);
    }

    @Transactional(rollbackFor = Exception.class)
    public void userDeleted(Long userId) {
        invalidate("operator_id=?", userId, true);
        invalidate("owner_id=?", userId, true);
    }
}
