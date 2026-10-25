"""Prepare ONLY new sidecar plugin files. Never starts/reconfigures production."""

import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / 'astrbot-data')
    args = parser.parse_args()
    versions = json.loads(Path(__file__).with_name('versions.json').read_text())
    target = args.data_dir / 'plugins' / 'astrbot_plugin_meme_manager'
    pin = versions['meme']
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', '--no-checkout', pin['repo'], str(target)], check=True)
        subprocess.run(['git', '-C', str(target), 'checkout', '--detach', pin['sha']], check=True)
    actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    changed = subprocess.check_output(['git', '-C', str(target), 'status', '--porcelain'], text=True).strip()
    if actual != pin['sha'] or changed:
        raise SystemExit('Existing Meme checkout differs; review it without overwriting user work.')
    print('Pinned Meme source ready; no bundled images, no containers started.')


if __name__ == '__main__':
    main()
