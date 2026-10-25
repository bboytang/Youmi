"""Install the emotion page into the already verified/patched manager source."""

from pathlib import Path
import shutil


def apply(root, check=False):
    source = Path(root) / 'main/manager-web/src'
    bundle = Path(__file__).parent
    replacements = {
        'router/index.js': ('const routes = [', "const routes = [\n  { path: '/affect-system', name: 'EmotionSystem', component: () => import('../views/EmotionSystem.vue'), meta: { requiresAuth: true, title: '情感系统' } },"),
        'components/HeaderBar.vue': ('v-show="!$responsive.compact || navigationOpen" :aria-label="$t(\'responsive.navigation\')">', 'v-show="!$responsive.compact || navigationOpen" :aria-label="$t(\'responsive.navigation\')">\n        <div class="equipment-management" :class="{ \'active-tab\': $route.path === \'/affect-system\' }" @click="$router.push(\'/affect-system\')"><span class="nav-text">情感系统</span></div>'),
        'components/DeviceItem.vue': ('<YoumiMemoryDialog v-model="youmiMemoryOpen" :agent-id="device.agentId" />', '<YoumiMemoryDialog v-model="youmiMemoryOpen" :agent-id="device.agentId" />\n    <button type="button" class="settings-btn" @click="$router.push({ path: \'/affect-system\', query: { agentId: device.agentId } })">情感系统</button>'),
    }
    changed = {}
    for name, (anchor, replacement) in replacements.items():
        path = source / name
        original = path.read_text()
        if original.count(anchor) != 1:
            raise ValueError('Emotional system source anchor differs: ' + name)
        changed[path] = original.replace(anchor, replacement, 1)
    if check:
        return
    for path, text in changed.items():
        path.write_text(text)
    shutil.copy2(bundle / 'EmotionSystem.vue', source / 'views/EmotionSystem.vue')
