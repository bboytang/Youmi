package xiaozhi.modules.device;

import static org.junit.jupiter.api.Assertions.*;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import xiaozhi.modules.security.service.SysUserTokenService;
import xiaozhi.modules.sys.service.SysParamsService;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"server.servlet.context-path=/youmi", "mia.private-auth.allow-insecure-test-http=true"})
@ActiveProfiles("dev")
class YoumiPrivateAuthorizationTest {
    private static final long USER_A = 930000001L;
    private static final long USER_B = 930000002L;
    private static final String AGENT_B = "mia-private-ci-b";
    private static final String DEVICE_B = "02:00:00:00:03:02";
    private static final String CLIENT = "mia-private-ci-client";
    @Autowired private TestRestTemplate http;
    @Autowired private JdbcTemplate db;
    @Autowired private StringRedisTemplate redis;
    @Autowired private SysUserTokenService tokens;
    @Autowired private SysParamsService params;
    @Autowired private ObjectMapper mapper;

    @BeforeEach
    void fixtures() {
        redis.delete(redis.keys("mia:private-auth:*"));
        db.update("DELETE FROM ai_mia_device_authorization WHERE device_id LIKE '02:00:00:00:03:%'");
        db.update("DELETE FROM ai_device WHERE id LIKE '02:00:00:00:03:%'");
        db.update("DELETE FROM ai_agent WHERE id=?", AGENT_B);
        db.update("DELETE FROM sys_user_token WHERE user_id IN (?,?)", USER_A, USER_B);
        db.update("DELETE FROM sys_user WHERE id IN (?,?)", USER_A, USER_B);
        for (long user : new long[]{USER_A, USER_B}) {
            db.update("INSERT INTO sys_user(id,username,super_admin,status) VALUES (?,?,0,1)", user, "mia-ci-" + user);
        }
        db.update("INSERT INTO ai_agent(id,user_id,agent_name) VALUES (?,?,?)", AGENT_B, USER_B, "CI only");
        db.update("INSERT INTO ai_device(id,mac_address,user_id,agent_id,board,auto_update) VALUES (?,?,?,?,?,0)",
                DEVICE_B, DEVICE_B, USER_B, AGENT_B, "MIA_CI");
    }

    private JsonNode post(String path, Map<String, Object> body, String token) throws Exception {
        return post(path, body, token, null);
    }

    private JsonNode post(String path, Map<String, Object> body, String token, String forwarded) throws Exception {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        if (token != null) headers.setBearerAuth(token);
        if (forwarded != null) headers.set("X-Forwarded-For", forwarded);
        var response = http.exchange(path, HttpMethod.POST, new HttpEntity<>(body, headers), String.class);
        assertNotNull(response.getBody());
        return mapper.readTree(response.getBody());
    }

    private String userToken(long user) {
        return tokens.createToken(user).getData().getToken();
    }

    private String code(String device, long user) throws Exception {
        JsonNode result = post("/device/mia-auth/code", Map.of("device_id", device), userToken(user));
        assertEquals(0, result.path("code").asInt());
        return result.path("data").path("authorization_code").asText();
    }

    private JsonNode exchange(String device, String client, String code) throws Exception {
        return post("/device/mia-auth/exchange", Map.of("device_id", device, "client_id", client,
                "authorization_code", code), null);
    }

    private JsonNode grant() throws Exception {
        JsonNode result = exchange(DEVICE_B, CLIENT, code(DEVICE_B, USER_B));
        assertEquals(0, result.path("code").asInt());
        return result.path("data");
    }

    private JsonNode verify(String device, String client, String credential) throws Exception {
        return post("/config/mia-auth/verify", Map.of("device_id", device, "client_id", client,
                "private_credential", credential), params.getValue("server.secret", false));
    }

    @Test
    void virtualBridgeUsesIndependentDeviceGrantAndRejectsOtherOwner() throws Exception {
        assertNotEquals(0, post("/device/mia-auth/virtual", Map.of("agent_id", AGENT_B, "bot_uin", "12345"), null).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/virtual", Map.of("agent_id", AGENT_B, "bot_uin", "12345"), userToken(USER_A)).path("code").asInt());
        JsonNode created = post("/device/mia-auth/virtual", Map.of("agent_id", AGENT_B, "bot_uin", "12345"), userToken(USER_B));
        assertEquals(0, created.path("code").asInt());
        String device = created.path("data").path("device_id").asText();
        try {
            assertTrue(device.startsWith("astrbot-"));
            assertEquals(32, device.length());
            assertNotEquals(device, DEVICE_B);
            String authorization = created.path("data").path("authorization").path("authorization_code").asText();
            JsonNode exchanged = exchange(device, "astrbot:12345", authorization);
            assertEquals(0, exchanged.path("code").asInt());
            String credential = exchanged.path("data").path("private_credential").asText();
            JsonNode verified = verify(device, "astrbot:12345", credential);
            assertEquals(0, verified.path("code").asInt());
            assertEquals("user:" + USER_B + ":agent:" + AGENT_B, verified.path("data").path("owner").asText());
            Map<String, Object> snapshot = new java.util.HashMap<>();
            for (String key : List.of("owner", "operator_id", "device_id", "client_id", "binding_version", "credential_version")) {
                snapshot.put(key, verified.path("data").path(key).asText());
            }
            String server = params.getValue("server.secret", false);
            assertNotEquals(0, post("/config/mia-auth/bridge-config", snapshot, userToken(USER_B)).path("code").asInt());
            JsonNode bridge = post("/config/mia-auth/bridge-config", snapshot, server);
            assertEquals(0, bridge.path("code").asInt());
            assertEquals(verified.path("data").path("owner"), bridge.path("data").path("mia_scope").path("owner"));
            assertEquals(bridge.path("data").path("mia_scope"), bridge.path("data").path("youmi_scope"));
            bridge.path("data").fieldNames().forEachRemaining(key -> assertTrue(
                    java.util.Set.of("selected_module", "LLM", "TTS", "prompt", "youmi_scope", "mia_scope").contains(key)));
            snapshot.put("owner", "user:" + USER_A + ":agent:" + AGENT_B);
            assertNotEquals(0, post("/config/mia-auth/bridge-config", snapshot, server).path("code").asInt());
            snapshot.put("owner", verified.path("data").path("owner").asText());
            assertNotEquals(0, verify(DEVICE_B, "astrbot:12345", credential).path("code").asInt());
            assertNotEquals(0, verify(device, CLIENT, credential).path("code").asInt());
            assertEquals(0, post("/device/mia-auth/revoke", Map.of("device_id", device), userToken(USER_B)).path("code").asInt());
            assertNotEquals(0, verify(device, "astrbot:12345", credential).path("code").asInt());
            assertNotEquals(0, post("/config/mia-auth/bridge-config", snapshot, server).path("code").asInt());
        } finally {
            db.update("DELETE FROM ai_mia_device_authorization WHERE device_id=?", device);
            db.update("DELETE FROM ai_device WHERE id=?", device);
        }
    }

    @Test
    void virtualRegistrationDoesNotAcceptCallerOwnedDeviceOrOwnerFields() throws Exception {
        for (Map<String, Object> body : List.<Map<String, Object>>of(
                Map.of("agent_id", AGENT_B, "bot_uin", "12345", "owner", "forged"),
                Map.of("agent_id", AGENT_B, "bot_uin", "12345", "device_id", DEVICE_B),
                Map.of("agent_id", AGENT_B, "bot_uin", "not-a-uin"))) {
            assertNotEquals(0, post("/device/mia-auth/virtual", body, userToken(USER_B)).path("code").asInt());
        }
    }

    @Test
    void identifiersAndOtherUserCannotAcquirePrivateGrant() throws Exception {
        assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B), null).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B), userToken(USER_A)).path("code").asInt());
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, "known-device-id-is-not-proof").path("code").asInt());
        assertEquals(0, db.queryForObject("SELECT COUNT(*) FROM ai_mia_device_authorization WHERE device_id=?", Integer.class, DEVICE_B));
        assertNotEquals(0, verify(DEVICE_B, CLIENT, "legacy-signature.timestamp").path("code").asInt());
    }

    @Test
    void physicalHardwareCodeRequiresCurrentOwnerAndUsesOriginalOneTimeExchange() throws Exception {
        String physicalCode = "89abcdef0123456789abcdef01234567";
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, physicalCode).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B,
                "hardware_code", physicalCode), userToken(USER_A)).path("code").asInt());
        assertEquals(0, db.queryForObject("SELECT COUNT(*) FROM ai_mia_device_authorization WHERE device_id=?", Integer.class, DEVICE_B));
        for (String invalid : List.of("123456", "g".repeat(32), "a".repeat(33))) {
            assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B,
                    "hardware_code", invalid), userToken(USER_B)).path("code").asInt());
        }
        assertEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B,
                "hardware_code", physicalCode), userToken(USER_B)).path("code").asInt());
        assertNotEquals(physicalCode, db.queryForObject("SELECT authorization_code_hash FROM ai_mia_device_authorization WHERE device_id=?", String.class, DEVICE_B));
        JsonNode issued = exchange(DEVICE_B, CLIENT, physicalCode);
        assertEquals(0, issued.path("code").asInt());
        assertEquals(USER_B, issued.path("data").path("scope").path("user_id").asLong());
        assertEquals(0, verify(DEVICE_B, CLIENT, issued.path("data").path("private_credential").asText()).path("code").asInt());
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, physicalCode).path("code").asInt());
    }

    @Test
    void sixDigitHardwareCodeApprovesOnlyTheRegisteredSecretAndClient() throws Exception {
        String proof = "0123456789abcdef0123456789abcdef";
        Map<String, Object> request = Map.of("device_id", DEVICE_B, "client_id", CLIENT,
                "authorization_code", proof, "hardware_pairing", true);
        JsonNode first = post("/device/mia-auth/exchange", request, null).path("data");
        String shortCode = first.path("pending_code").asText();
        assertTrue(shortCode.matches("[0-9]{6}"));
        assertFalse(first.has("private_credential"));
        assertFalse(first.has("scope"));
        assertEquals(shortCode, post("/device/mia-auth/exchange", request, null).path("data").path("pending_code").asText());
        for (String key : redis.keys("mia:private-auth:hardware:*")) {
            assertFalse(redis.opsForValue().get(key).contains(proof));
        }
        assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B,
                "hardware_code", shortCode), userToken(USER_A)).path("code").asInt());
        assertEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B,
                "hardware_code", shortCode), userToken(USER_B)).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/exchange", Map.of("device_id", DEVICE_B,
                "client_id", CLIENT + "-other", "authorization_code", proof,
                "hardware_pairing", true), null).path("code").asInt());
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, "hardware:" + proof).path("code").asInt());
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, shortCode).path("code").asInt());
        db.update("UPDATE ai_mia_device_authorization SET code_expires_at=UTC_TIMESTAMP()-INTERVAL 1 SECOND WHERE device_id=?", DEVICE_B);
        assertFalse(post("/device/mia-auth/exchange", request, null).path("data").has("private_credential"));
        assertEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B,
                "hardware_code", shortCode), userToken(USER_B)).path("code").asInt());
        JsonNode issued = post("/device/mia-auth/exchange", request, null);
        assertEquals(0, issued.path("code").asInt());
        String credential = issued.path("data").path("private_credential").asText();
        assertTrue(credential.matches("[A-Za-z0-9_-]{43}"));
        assertEquals(0, verify(DEVICE_B, CLIENT, credential).path("code").asInt());
        JsonNode replay = post("/device/mia-auth/exchange", request, null).path("data");
        assertFalse(replay.has("private_credential"));
        String stale = replay.path("pending_code").asText();
        assertEquals(0, post("/device/mia-auth/revoke", Map.of("device_id", DEVICE_B), userToken(USER_B)).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B,
                "hardware_code", stale), userToken(USER_B)).path("code").asInt());
        assertNotEquals(0, verify(DEVICE_B, CLIENT, credential).path("code").asInt());
    }

    @Test
    void codeIsConsumedAtomicallyAndOnlyHashesAreStored() throws Exception {
        String code = code(DEVICE_B, USER_B);
        assertTrue(code.length() >= 22);
        assertNotEquals(code, db.queryForObject("SELECT authorization_code_hash FROM ai_mia_device_authorization WHERE device_id=?", String.class, DEVICE_B));
        var pool = Executors.newFixedThreadPool(2);
        var start = new CountDownLatch(1);
        List<JsonNode> responses = new ArrayList<>();
        try {
            var first = pool.submit(() -> { start.await(); return exchange(DEVICE_B, CLIENT + "-1", code); });
            var second = pool.submit(() -> { start.await(); return exchange(DEVICE_B, CLIENT + "-2", code); });
            start.countDown();
            responses.add(first.get(15, TimeUnit.SECONDS));
            responses.add(second.get(15, TimeUnit.SECONDS));
        } finally {
            pool.shutdownNow();
        }
        assertEquals(1, responses.stream().filter(r -> r.path("code").asInt() == 0).count());
        JsonNode value = responses.stream().filter(r -> r.path("code").asInt() == 0).findFirst().orElseThrow().path("data");
        String credential = value.path("private_credential").asText();
        assertTrue(credential.length() >= 43);
        assertNull(db.queryForObject("SELECT authorization_code_hash FROM ai_mia_device_authorization WHERE device_id=?", String.class, DEVICE_B));
        String hash = db.queryForObject("SELECT credential_hash FROM ai_mia_device_authorization WHERE device_id=?", String.class, DEVICE_B);
        assertTrue(hash.matches("[a-f0-9]{64}"));
        assertNotEquals(credential, hash);
        assertEquals(0, verify(DEVICE_B, value.path("scope").path("client_id").asText(), credential).path("code").asInt());
    }

    @Test
    void expiryAndFiveFailedAttemptsAreEnforcedAndCommitted() throws Exception {
        String expired = code(DEVICE_B, USER_B);
        db.update("UPDATE ai_mia_device_authorization SET code_expires_at=UTC_TIMESTAMP()-INTERVAL 1 SECOND WHERE device_id=?", DEVICE_B);
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, expired).path("code").asInt());
        String current = code(DEVICE_B, USER_B);
        for (int i = 0; i < 5; i++) assertNotEquals(0, exchange(DEVICE_B, CLIENT, "incorrect-" + i).path("code").asInt());
        assertEquals(5, db.queryForObject("SELECT wrong_attempts FROM ai_mia_device_authorization WHERE device_id=?", Integer.class, DEVICE_B));
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, current).path("code").asInt());
        assertEquals(0, exchange(DEVICE_B, CLIENT, code(DEVICE_B, USER_B)).path("code").asInt());
    }

    @Test
    void scopeComesFromBindingAndRejectsDifferentDeviceClientOrExpiredCredential() throws Exception {
        JsonNode grant = grant();
        String credential = grant.path("private_credential").asText();
        JsonNode scope = grant.path("scope");
        assertEquals(USER_B, scope.path("user_id").asLong());
        assertEquals(USER_B, scope.path("operator_id").asLong());
        assertEquals("user:" + USER_B + ":agent:" + AGENT_B, scope.path("owner").asText());
        assertEquals("mia", scope.path("persona_id").asText());
        assertNotEquals(0, verify(DEVICE_B, "forged-client", credential).path("code").asInt());
        assertNotEquals(0, verify("02:00:00:00:03:99", CLIENT, credential).path("code").asInt());
        assertNotEquals(0, post("/config/mia-auth/verify", Map.of("device_id", DEVICE_B, "client_id", CLIENT,
                "private_credential", credential), userToken(USER_B)).path("code").asInt());
        db.update("UPDATE ai_mia_device_authorization SET credential_expires_at=UTC_TIMESTAMP()-INTERVAL 1 SECOND WHERE device_id=?", DEVICE_B);
        assertNotEquals(0, verify(DEVICE_B, CLIENT, credential).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/renew", Map.of("device_id", DEVICE_B, "client_id", CLIENT,
                "private_credential", credential), null).path("code").asInt());
    }

    @Test
    void renewalRotatesAndRevocationRejectsBothTokensAndCode() throws Exception {
        JsonNode original = grant();
        String old = original.path("private_credential").asText();
        JsonNode renewed = post("/device/mia-auth/renew", Map.of("device_id", DEVICE_B, "client_id", CLIENT,
                "private_credential", old), null);
        assertEquals(0, renewed.path("code").asInt());
        String current = renewed.path("data").path("private_credential").asText();
        assertNotEquals(old, current);
        assertTrue(renewed.path("data").path("scope").path("credential_version").asLong()
                > original.path("scope").path("credential_version").asLong());
        assertNotEquals(0, verify(DEVICE_B, CLIENT, old).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/revoke", Map.of("device_id", DEVICE_B), userToken(USER_A)).path("code").asInt());
        assertEquals(0, verify(DEVICE_B, CLIENT, current).path("code").asInt());
        String pendingCode = code(DEVICE_B, USER_B);
        assertEquals(0, post("/device/mia-auth/revoke", Map.of("device_id", DEVICE_B), userToken(USER_B)).path("code").asInt());
        assertNotEquals(0, verify(DEVICE_B, CLIENT, current).path("code").asInt());
        assertNotEquals(0, exchange(DEVICE_B, CLIENT, pendingCode).path("code").asInt());
    }

    @Test
    void unbindAndRebindSameDeviceCannotReviveOldGrant() throws Exception {
        JsonNode original = grant();
        String old = original.path("private_credential").asText();
        long version = original.path("scope").path("binding_version").asLong();
        assertEquals(0, post("/device/unbind", Map.of("deviceId", DEVICE_B), userToken(USER_B)).path("code").asInt());
        assertNotEquals(0, verify(DEVICE_B, CLIENT, old).path("code").asInt());
        assertEquals(0, post("/device/manual-add", Map.of("agentId", AGENT_B, "macAddress", DEVICE_B,
                "board", "MIA_CI", "appVersion", "CI"), userToken(USER_B)).path("code").asInt());
        JsonNode renewed = grant();
        assertEquals(DEVICE_B, renewed.path("scope").path("device_id").asText());
        assertTrue(renewed.path("scope").path("binding_version").asLong() > version);
        assertNotEquals(0, verify(DEVICE_B, CLIENT, old).path("code").asInt());
    }

    @Test
    void administratorOperatorAndDataOwnerRemainDistinctAndCurrentRoleIsChecked() throws Exception {
        String device = "02:00:00:00:03:03";
        db.update("UPDATE sys_user SET super_admin=1 WHERE id=?", USER_A);
        assertEquals(0, post("/device/manual-add", Map.of("agentId", AGENT_B, "macAddress", device,
                "board", "MIA_CI", "appVersion", "CI"), userToken(USER_A)).path("code").asInt());
        JsonNode value = exchange(device, CLIENT, code(device, USER_A)).path("data");
        String credential = value.path("private_credential").asText();
        assertEquals(USER_A, value.path("scope").path("operator_id").asLong());
        assertEquals(USER_B, value.path("scope").path("user_id").asLong());
        assertEquals(0, verify(device, CLIENT, credential).path("code").asInt());
        db.update("UPDATE sys_user SET super_admin=0 WHERE id=?", USER_A);
        assertNotEquals(0, verify(device, CLIENT, credential).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", device), userToken(USER_A)).path("code").asInt());
    }

    @Test
    void disabledOwnerOrChangedBindingFailsCurrentAuthorization() throws Exception {
        String credential = grant().path("private_credential").asText();
        db.update("UPDATE sys_user SET status=0 WHERE id=?", USER_B);
        assertNotEquals(0, verify(DEVICE_B, CLIENT, credential).path("code").asInt());
        db.update("UPDATE sys_user SET status=1 WHERE id=?", USER_B);
        db.update("UPDATE ai_device SET user_id=? WHERE id=?", USER_A, DEVICE_B);
        assertNotEquals(0, verify(DEVICE_B, CLIENT, credential).path("code").asInt());
        assertNotEquals(0, post("/device/mia-auth/code", Map.of("device_id", DEVICE_B), userToken(USER_A)).path("code").asInt());
    }

    @Test
    void trustedSnapshotCheckRequiresServerSecretAndRejectsOwnerVersionAndRevocation() throws Exception {
        JsonNode original = grant();
        JsonNode scope = original.path("scope");
        Map<String, Object> snapshot = new java.util.HashMap<>(Map.of("device_id", DEVICE_B, "client_id", CLIENT,
                "owner", scope.path("owner").asText(), "operator_id", scope.path("operator_id").asLong(),
                "binding_version", scope.path("binding_version").asLong(),
                "credential_version", scope.path("credential_version").asLong()));
        String server = params.getValue("server.secret", false);
        assertNotEquals(0, post("/config/mia-auth/check-scope", snapshot, userToken(USER_B)).path("code").asInt());
        assertEquals(0, post("/config/mia-auth/check-scope", snapshot, server).path("code").asInt());
        assertNotEquals(0, post("/config/mia-auth/bridge-config", snapshot, server).path("code").asInt());
        snapshot.put("owner", "user:" + USER_A + ":agent:" + AGENT_B);
        assertNotEquals(0, post("/config/mia-auth/check-scope", snapshot, server).path("code").asInt());
        snapshot.put("owner", scope.path("owner").asText());
        snapshot.put("credential_version", scope.path("credential_version").asLong() + 1);
        assertNotEquals(0, post("/config/mia-auth/check-scope", snapshot, server).path("code").asInt());
        snapshot.put("credential_version", scope.path("credential_version").asLong());
        snapshot.put("binding_version", 0.5);
        assertNotEquals(0, post("/config/mia-auth/check-scope", snapshot, server).path("code").asInt());
        snapshot.put("binding_version", scope.path("binding_version").asLong());
        assertEquals(0, post("/device/mia-auth/revoke", Map.of("device_id", DEVICE_B), userToken(USER_B)).path("code").asInt());
        assertNotEquals(0, post("/config/mia-auth/check-scope", snapshot, server).path("code").asInt());
        assertEquals(USER_B, grant().path("scope").path("user_id").asLong());
        assertNotEquals(0, post("/config/mia-auth/check-scope", snapshot, server).path("code").asInt());
    }

    @Test
    void personalizedConfigAndCorrectWordsRequirePrivateGrantAndKeepOriginalBinding() throws Exception {
        String server = params.getValue("server.secret", false);
        db.update("UPDATE ai_agent SET system_prompt=?,summary_memory=? WHERE id=?",
                "CI private prompt marker", "CI private summary marker", AGENT_B);
        Map<String, Object> config = new java.util.HashMap<>(Map.of("macAddress", DEVICE_B,
                "clientId", CLIENT, "selectedModule", Map.of()));
        Map<String, Object> words = new java.util.HashMap<>(Map.of("macAddress", DEVICE_B, "clientId", CLIENT));
        for (String invalid : new String[]{"", "legacy-signature.timestamp", "known-device-id-is-not-proof"}) {
            config.put("privateCredential", invalid);
            words.put("privateCredential", invalid);
            for (JsonNode response : List.of(post("/config/agent-models", config, server),
                    post("/config/correct-words", words, server))) {
                assertNotEquals(0, response.path("code").asInt());
                assertFalse(response.toString().contains("CI private"));
            }
        }
        String credential = grant().path("private_credential").asText();
        config.put("privateCredential", credential);
        words.put("privateCredential", credential);
        JsonNode authorized = post("/config/agent-models", config, server);
        assertEquals(0, authorized.path("code").asInt());
        assertTrue(authorized.toString().contains("CI private prompt marker"));
        assertTrue(authorized.toString().contains("CI private summary marker"));
        assertEquals("user:" + USER_B + ":agent:" + AGENT_B, authorized.path("data").path("mia_scope").path("owner").asText());
        assertEquals(0, post("/config/correct-words", words, server).path("code").asInt());
        assertNotEquals(0, post("/config/agent-models", config, userToken(USER_B)).path("code").asInt());
        words.put("clientId", "other-client");
        assertNotEquals(0, post("/config/correct-words", words, server).path("code").asInt());
        assertEquals(0, post("/device/mia-auth/revoke", Map.of("device_id", DEVICE_B), userToken(USER_B)).path("code").asInt());
        assertNotEquals(0, post("/config/agent-models", config, server).path("code").asInt());
        assertEquals(USER_B, db.queryForObject("SELECT user_id FROM ai_device WHERE id=?", Long.class, DEVICE_B));
        assertEquals(AGENT_B, db.queryForObject("SELECT agent_id FROM ai_device WHERE id=?", String.class, DEVICE_B));
    }

    @Test
    void pythonScopeUsesActualManagerProofAndRejectsRevokedSnapshots() throws Exception {
        db.update("UPDATE ai_agent SET system_prompt=?,llm_model_id=? WHERE id=?", "CI private prompt marker", "LLM_ChatGLMLLM", AGENT_B);
        JsonNode value = grant();
        String script = System.getenv("MIA_SCOPE_INTEGRATION_SCRIPT");
        assertNotNull(script, "The identity workflow must provide the actual Python integration entry");
        ProcessBuilder process = new ProcessBuilder("python", script).redirectErrorStream(true);
        var env = process.environment();
        env.put("MIA_SCOPE_MANAGER_URL", http.getRootUri().replace("localhost", "127.0.0.1") + "/");
        env.put("MIA_SCOPE_SERVER_SECRET", params.getValue("server.secret", false));
        env.put("MIA_SCOPE_DEVICE", DEVICE_B);
        env.put("MIA_SCOPE_CLIENT", CLIENT);
        env.put("MIA_SCOPE_CREDENTIAL", value.path("private_credential").asText());
        env.put("MIA_SCOPE_USER_TOKEN", userToken(USER_B));
        Process running = process.start();
        if (!running.waitFor(30, TimeUnit.SECONDS)) {
            running.destroyForcibly();
            fail("Python manager Scope integration timed out");
        }
        String output = new String(running.getInputStream().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
        assertEquals(0, running.exitValue(), output);
        assertTrue(output.contains("\"manager_python_scope_pass\": true"), output);
    }

    @Test
    void forwardedHeaderCannotBypassBoundedExchangeRateLimit() throws Exception {
        long second = Instant.now().getEpochSecond() % 60;
        if (second > 55) Thread.sleep((60 - second) * 1000 + 100);
        code(DEVICE_B, USER_B);
        for (int i = 0; i < 20; i++) {
            assertNotEquals(0, post("/device/mia-auth/exchange", Map.of("device_id", DEVICE_B, "client_id", CLIENT,
                    "authorization_code", "invalid-no-grant"), null, "192.0.2." + i).path("code").asInt());
        }
        String valid = code(DEVICE_B, USER_B);
        assertNotEquals(0, post("/device/mia-auth/exchange", Map.of("device_id", DEVICE_B, "client_id", CLIENT,
                "authorization_code", valid), null, "198.51.100.1").path("code").asInt());
        assertEquals(1, redis.keys("mia:private-auth:source:*").size());
        assertEquals("21", redis.opsForValue().get(redis.keys("mia:private-auth:source:*").iterator().next()));
        assertNull(db.queryForObject("SELECT credential_hash FROM ai_mia_device_authorization WHERE device_id=?", String.class, DEVICE_B));
        assertEquals(0, db.queryForObject("SELECT wrong_attempts FROM ai_mia_device_authorization WHERE device_id=?", Integer.class, DEVICE_B));
    }
}
