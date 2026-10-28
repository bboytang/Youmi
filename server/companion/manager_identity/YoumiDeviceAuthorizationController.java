package xiaozhi.modules.device.controller;

import java.util.Map;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.utils.Result;
import xiaozhi.modules.config.service.ConfigService;
import xiaozhi.modules.device.service.impl.YoumiDeviceAuthorizationService;
import xiaozhi.modules.security.service.SysUserTokenService;
import xiaozhi.modules.sys.service.SysParamsService;
import xiaozhi.modules.sys.service.SysUserService;

@RestController
@RequiredArgsConstructor
public class YoumiDeviceAuthorizationController {
    private final YoumiDeviceAuthorizationService grants;
    private final ConfigService configService;
    private final SysUserTokenService tokens;
    private final SysParamsService params;
    private final SysUserService users;
    private final ObjectMapper mapper;
    @Value("${youmi.private-auth.allow-insecure-test-http:${mia.private-auth.allow-insecure-test-http:false}}")
    private boolean allowInsecureTestHttp;

    private void secure(HttpServletRequest request) {
        if (!request.isSecure() && !allowInsecureTestHttp) throw new RenException(ErrorCode.FORBIDDEN);
    }

    private String field(Map<String, Object> body, String key, int maximum) {
        Object value = body.get(key);
        if (!(value instanceof String text) || text.isBlank() || text.length() > maximum
                || !text.matches("[A-Za-z0-9_:.\\-]+")) throw new RenException(ErrorCode.NO_PERMISSION);
        return (String) value;
    }

    private Result<Map<String, Object>> result(Map<String, Object> value) {
        if (value == null) throw new RenException(ErrorCode.NO_PERMISSION);
        return new Result<Map<String, Object>>().ok(value);
    }

    private long version(Map<String, Object> body, String key) {
        Object value = body.get(key);
        String text = value == null ? "" : value.toString();
        if (!text.matches("[0-9]{1,19}")) throw new RenException(ErrorCode.NO_PERMISSION);
        try {
            return Long.parseLong(text);
        } catch (NumberFormatException invalid) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
    }

    @PostMapping({"/device/youmi-auth/code", "/device/mia-auth/code"})
    @RequiresPermissions("sys:role:normal")
    public Result<Map<String, Object>> code(@RequestBody Map<String, Object> body, HttpServletRequest request) {
        secure(request);
        if (body.containsKey("hardware_code")) {
            return result(grants.approveHardwareCode(field(body, "device_id", 32), field(body, "hardware_code", 32)));
        }
        return result(grants.createCode(field(body, "device_id", 32)));
    }

    @PostMapping({"/device/youmi-auth/exchange", "/device/mia-auth/exchange"})
    public Result<Map<String, Object>> exchange(@RequestBody Map<String, Object> body, HttpServletRequest request) {
        secure(request);
        if (Boolean.TRUE.equals(body.get("hardware_pairing"))) {
            return result(grants.pollHardware(field(body, "device_id", 32), field(body, "client_id", 128),
                    field(body, "authorization_code", 64), request.getRemoteAddr()));
        }
        return result(grants.exchange(field(body, "device_id", 32), field(body, "client_id", 128),
                field(body, "authorization_code", 64), request.getRemoteAddr()));
    }

    @PostMapping({"/device/youmi-auth/virtual", "/device/mia-auth/virtual"})
    @RequiresPermissions("sys:role:normal")
    public Result<Map<String, Object>> virtual(@RequestBody Map<String, Object> body, HttpServletRequest request) {
        secure(request);
        if (!body.keySet().equals(java.util.Set.of("agent_id", "bot_uin"))) throw new RenException(ErrorCode.NO_PERMISSION);
        return result(grants.createVirtual(field(body, "agent_id", 32), field(body, "bot_uin", 20)));
    }

    @PostMapping({"/config/youmi-auth/bridge-config", "/config/mia-auth/bridge-config"})
    @Transactional(rollbackFor = Exception.class)
    public Result<Map<String, Object>> bridgeConfig(@RequestBody Map<String, Object> snapshot) {
        Map<String, Object> scope = grants.requireSnapshot(snapshot, true);
        grants.requireVirtual(scope);
        return bridgeModels(scope);
    }

    @PostMapping({"/config/youmi-auth/passport-config", "/config/mia-auth/passport-config"})
    @Transactional(rollbackFor = Exception.class)
    public Result<Map<String, Object>> passportConfig(@RequestBody Map<String, Object> snapshot) {
        Map<String, Object> scope = grants.requireSnapshot(snapshot, true);
        grants.requirePhysical(scope);
        return bridgeModels(scope);
    }

    private Result<Map<String, Object>> bridgeModels(Map<String, Object> scope) {
        Map<String, Object> models = configService.getAgentModels((String) scope.get("device_id"), Map.of());
        if (!scope.get("owner").equals(models.get("mia_owner"))) throw new RenException(ErrorCode.NO_PERMISSION);
        Map<String, Object> allowed = new java.util.HashMap<>();
        for (String key : java.util.List.of("selected_module", "LLM", "TTS", "prompt")) {
            if (models.containsKey(key)) allowed.put(key, models.get(key));
        }
        allowed.put("mia_scope", scope);
        allowed.put("youmi_scope", scope);
        return result(allowed);
    }

    /** A revocable, non-expiring delegation issues ordinary short-lived native sessions. */
    @PostMapping({"/config/youmi-auth/admin-session", "/config/mia-auth/admin-session"})
    @Transactional(rollbackFor = Exception.class)
    public Result<Map<String, Object>> adminSession(@RequestBody Map<String, Object> snapshot) {
        Map<String, Object> scope = grants.requireSnapshot(snapshot, true);
        grants.requireVirtual(scope);
        try {
            String configured = params.getValue("youmi.autonomous_admin.owners", false);
            var owners = mapper.readTree(configured == null ? "[]" : configured);
            boolean delegated = false;
            if (owners != null && owners.isArray()) {
                for (var owner : owners) {
                    if (owner.isTextual() && owner.asText().equals(scope.get("owner"))) delegated = true;
                }
            }
            long operator = ((Number) scope.get("operator_id")).longValue();
            var user = users.getByUserId(operator);
            if (!delegated || user == null || !Integer.valueOf(1).equals(user.getStatus())
                    || !Integer.valueOf(1).equals(user.getSuperAdmin())) {
                throw new RenException(ErrorCode.NO_PERMISSION);
            }
            var session = tokens.createToken(operator).getData();
            return result(Map.of("token", session.getToken(), "expires_in", session.getExpire()));
        } catch (java.io.IOException invalid) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
    }

    @PostMapping({"/device/youmi-auth/renew", "/device/mia-auth/renew"})
    public Result<Map<String, Object>> renew(@RequestBody Map<String, Object> body, HttpServletRequest request) {
        secure(request);
        return result(grants.renew(field(body, "device_id", 32), field(body, "client_id", 128),
                field(body, "private_credential", 64), request.getRemoteAddr()));
    }

    @PostMapping({"/device/youmi-auth/revoke", "/device/mia-auth/revoke"})
    @RequiresPermissions("sys:role:normal")
    public Result<Void> revoke(@RequestBody Map<String, Object> body, HttpServletRequest request) {
        secure(request);
        grants.revoke(field(body, "device_id", 32));
        return new Result<>();
    }

    @PostMapping({"/config/youmi-auth/verify", "/config/mia-auth/verify"})
    public Result<Map<String, Object>> verify(@RequestBody Map<String, Object> body) {
        return result(grants.verify(field(body, "device_id", 32), field(body, "client_id", 128),
                field(body, "private_credential", 64)));
    }

    @PostMapping({"/config/youmi-auth/check-scope", "/config/mia-auth/check-scope"})
    public Result<Map<String, Object>> checkScope(@RequestBody Map<String, Object> body) {
        return result(grants.checkScope(field(body, "device_id", 32), field(body, "client_id", 128),
                field(body, "owner", 256), version(body, "operator_id"),
                version(body, "binding_version"), version(body, "credential_version")));
    }

    @PostMapping({"/config/youmi-auth/worker-model", "/config/mia-auth/worker-model"})
    @Transactional(rollbackFor = Exception.class)
    public Result<Map<String, Object>> workerModel(@RequestBody Map<String, Object> snapshot) {
        Map<String, Object> scope = grants.requireSnapshot(snapshot, true);
        Map<String, Object> models = configService.getAgentModels((String) scope.get("device_id"), Map.of());
        if (!scope.get("owner").equals(models.get("mia_owner"))) throw new RenException(ErrorCode.NO_PERMISSION);
        if (!(models.get("selected_module") instanceof Map<?, ?> selected)
                || !(selected.get("LLM") instanceof String module)
                || !(models.get("LLM") instanceof Map<?, ?> llms) || llms.get(module) == null) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        // Only the currently authorized agent's existing LLM, never a client-selected model.
        return result(Map.of("youmi_scope", scope, "mia_scope", scope, "selected_module", Map.of("LLM", module),
                "LLM", Map.of(module, llms.get(module))));
    }
}
