package xiaozhi.modules.device.controller;

import java.util.Map;
import java.util.Set;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.client.HttpClientErrorException;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import xiaozhi.common.constant.Constant;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.utils.Result;
import xiaozhi.modules.agent.service.AgentService;
import xiaozhi.modules.security.user.SecurityUser;
import xiaozhi.modules.sys.service.SysParamsService;

/** The existing console login owns management; browser-supplied owners are never trusted. */
@RestController
@RequiredArgsConstructor
public class YoumiCompanionManagementController {
    private final AgentService agents;
    private final SysParamsService params;
    private final ObjectMapper mapper;
    private final JdbcTemplate db;
    @Value("${youmi.companion.management-url:${mia.companion.management-url:http://xiaozhi-esp32-server:8003/youmi/management}}")
    private String managementUrl;
    @Value("${youmi.private-auth.allow-insecure-test-http:${mia.private-auth.allow-insecure-test-http:false}}")
    private boolean allowInsecureTestHttp;

    private Map<String, String> grant(String agentId, long operator) {
        if (agentId == null || !agentId.matches("[A-Za-z0-9_-]{1,64}")) {
            throw new RenException(ErrorCode.PARAMS_GET_ERROR);
        }
        if (db.queryForObject("SELECT COUNT(*) FROM sys_user WHERE id=? AND status=1", Integer.class, operator) != 1) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        var agent = agents.getAgentById(agentId, operator);
        // Private companion data remains owner-only, including for console administrators.
        if (agent.getUserId() == null || agent.getUserId().longValue() != operator) {
            throw new RenException(ErrorCode.NO_PERMISSION);
        }
        return Map.of("owner", "user:" + agent.getUserId() + ":agent:" + agentId);
    }

    @PostMapping({"/config/youmi-management/verify", "/config/mia-management/verify"})
    public Result<Map<String, String>> verify(@RequestBody Map<String, Object> body) {
        try {
            String agentId = (String) body.get("agent_id");
            String operator = String.valueOf(body.get("operator_id"));
            if (!operator.matches("[1-9][0-9]{0,18}")) throw new IllegalArgumentException();
            return new Result<Map<String, String>>().ok(grant(agentId, Long.parseLong(operator)));
        } catch (ClassCastException | IllegalArgumentException invalid) {
            throw new RenException(ErrorCode.PARAMS_GET_ERROR);
        }
    }

    @PostMapping({"/agent/{agentId}/youmi-settings", "/agent/{agentId}/mia-settings"})
    @RequiresPermissions("sys:role:normal")
    public Result<Object> manage(@PathVariable String agentId, @RequestBody Map<String, Object> command,
                                 HttpServletRequest request) {
        if (!request.isSecure() && !allowInsecureTestHttp) throw new RenException(ErrorCode.FORBIDDEN);
        long operator = SecurityUser.getUserId();
        grant(agentId, operator);
        if (!(command.get("action") instanceof String action)
                || !Set.of("status", "pause", "set_feature_settings", "emotion_consent", "reset_youmi_emotion", "reset_mia_emotion", "memory_list", "memory_delete", "set_character", "affect_system", "memory_system", "notebook").contains(action)) {
            throw new RenException(ErrorCode.PARAMS_GET_ERROR);
        }
        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(2000);
        factory.setReadTimeout(10000);
        var headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(params.getValue(Constant.SERVER_SECRET, true));
        try {
            String response = new RestTemplate(factory).postForObject(managementUrl,
                new HttpEntity<>(Map.of("operator_id", operator, "agent_id", agentId, "request", command), headers), String.class);
            var result = mapper.readTree(response);
            if (!result.has("code") || result.get("code").asInt() != 0 || !result.has("data")) {
                throw new IllegalStateException();
            }
            grant(agentId, operator);
            return new Result<>().ok(mapper.convertValue(result.get("data"), Object.class));
        } catch (HttpClientErrorException rejected) {
            grant(agentId, operator);
            if (action.equals("memory_system") && rejected.getStatusCode().value() == 409) {
                return new Result<>().error(409, "记忆或配置已变化，请保留草稿并刷新后核对");
            }
            if (action.equals("memory_system") && rejected.getStatusCode().value() == 400) {
                return new Result<>().error(ErrorCode.PARAMS_GET_ERROR, "记忆操作未完成，请核对参数或来源是否仍然有效");
            }
            throw new RenException(ErrorCode.INTERNAL_SERVER_ERROR, "Youmi 管理服务暂不可用，操作结果未确认，请刷新重试");
        } catch (Exception unavailable) {
            throw new RenException(ErrorCode.INTERNAL_SERVER_ERROR, "Youmi 管理服务暂不可用，操作结果未确认，请刷新重试");
        }
    }

    @PostMapping("/agent/{agentId}/affect-system")
    @RequiresPermissions("sys:role:normal")
    public Result<Object> affectSystem(@PathVariable String agentId, @RequestBody Map<String, Object> operation,
                                      HttpServletRequest request) {
        return manage(agentId, Map.of("action", "affect_system", "operation", operation), request);
    }

    @PostMapping("/agent/{agentId}/memory-system")
    @RequiresPermissions("sys:role:normal")
    public Result<Object> memorySystem(@PathVariable String agentId, @RequestBody Map<String, Object> operation,
                                      HttpServletRequest request) {
        return manage(agentId, Map.of("action", "memory_system", "operation", operation), request);
    }

    @PostMapping("/agent/{agentId}/notebook")
    @RequiresPermissions("sys:role:normal")
    public Result<Object> notebook(@PathVariable String agentId, @RequestBody Map<String, Object> operation,
                                   HttpServletRequest request, HttpServletResponse response) {
        response.setHeader("Cache-Control", "no-store");
        return manage(agentId, Map.of("action", "notebook", "operation", operation), request);
    }
}
