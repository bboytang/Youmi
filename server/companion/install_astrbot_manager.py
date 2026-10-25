"""Offline legacy-name build and strictly scoped JAR patch; never modifies a running service."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


PREFIX = 'BOOT-INF/classes/xiaozhi/modules/device/'
PREIMAGES = {
    PREFIX + 'controller/MiaDeviceAuthorizationController.class': '3d7b75e9d0449fa73a99858223815642a120c9511a37d868a903419c7fc52530',
    PREFIX + 'service/impl/MiaDeviceAuthorizationService.class': 'd169784e9b60d5c95de210b202383e804c56ee497a0cfbb5fb356abc6c3c4917',
    PREFIX + 'service/impl/MiaDeviceAuthorizationService$Binding.class': '893f6e4a8e7e75112a855e2978a15ccb9be3910d88ac00e8372e480645514262',
}
TYPES = ('DeviceAuthorizationController', 'DeviceAuthorizationService')


def legacy_sources(upstream):
    """Convert only our two Java type names in a freshly installed offline build tree."""
    source = upstream / 'main/manager-api/src'
    targets = {'DeviceAuthorizationController': 'controller', 'DeviceAuthorizationService': 'service/impl'}
    for name, package in targets.items():
        folder = source / 'main/java/xiaozhi/modules/device' / package
        current, legacy = folder / ('Youmi' + name + '.java'), folder / ('Mia' + name + '.java')
        expected = Path(__file__).with_name('manager_identity') / current.name
        if not current.is_file() or current.read_bytes() != expected.read_bytes() or legacy.exists():
            raise ValueError('Unreviewed or already converted manager source: ' + str(current))
    changes = {}
    for path in source.rglob('*.java'):
        before = path.read_text(encoding='utf-8')
        after = before
        for name in TYPES:
            after = after.replace('Youmi' + name, 'Mia' + name)
        if after != before:
            destination = path.with_name(path.name.replace('YoumiDeviceAuthorization', 'MiaDeviceAuthorization'))
            changes[path] = (destination, after)
    return changes


def package(classes, output):
    payload = {}
    for name in PREIMAGES:
        path = classes / name.removeprefix('BOOT-INF/classes/')
        data = path.read_bytes()
        if data[:4] != b'\xca\xfe\xba\xbe' or int.from_bytes(data[6:8], 'big') != 65:
            raise ValueError('Expected compiled Java 21 legacy class: ' + name)
        if b'YoumiDeviceAuthorization' in data:
            raise ValueError('Current-name class cannot be overlaid on the legacy runtime')
        payload[name] = data
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).parents[2], text=True).strip()
    manifest = {'protocol_version': 1, 'source_sha': revision, 'expected': PREIMAGES,
                'replacement': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
    with output.open('xb') as stream, zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest, sort_keys=True, indent=2))
        for name, data in payload.items():
            archive.writestr(name, data)
    return manifest


def entry_digest(archive, name):
    digest = hashlib.sha256()
    with archive.open(name) as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def apply(original, patch, output):
    if output.exists() or original.resolve() == output.resolve():
        raise ValueError('Output must be a new candidate JAR, never the original')
    with zipfile.ZipFile(patch) as incoming, zipfile.ZipFile(original) as before:
        manifest = json.loads(incoming.read('manifest.json'))
        names = incoming.namelist()
        if (manifest.get('protocol_version') != 1 or manifest.get('expected') != PREIMAGES
                or set(manifest.get('replacement', {})) != set(PREIMAGES)
                or len(names) != len(set(names)) or set(names) != {'manifest.json', *PREIMAGES}):
            raise ValueError('Unreviewed patch manifest or entries')
        originals = before.namelist()
        if len(originals) != len(set(originals)):
            raise ValueError('Duplicate original JAR entries')
        if any(name.startswith('META-INF/') and name.upper().endswith(('.RSA', '.DSA', '.EC')) for name in originals):
            raise ValueError('Signed JAR requires a separately reviewed signing process')
        for name, expected in PREIMAGES.items():
            if name not in originals or entry_digest(before, name) != expected:
                raise ValueError('Unreviewed production class preimage: ' + name)
        if any('YoumiDeviceAuthorization' in name for name in originals):
            raise ValueError('Mixed legacy/current authorization classes')
        replacements = {name: incoming.read(name) for name in PREIMAGES}
        if any(hashlib.sha256(data).hexdigest() != manifest['replacement'][name]
               for name, data in replacements.items()):
            raise ValueError('Patch payload checksum mismatch')
        with tempfile.TemporaryDirectory(prefix='youmi-manager-', dir=output.parent) as temporary:
            candidate = Path(temporary) / 'candidate.jar'
            offset = min(info.header_offset for info in before.infolist())
            with original.open('rb') as stream:
                preamble = stream.read(offset)
            with candidate.open('wb') as stream:
                stream.write(preamble)
                with zipfile.ZipFile(stream, 'w') as after:
                    after.comment = before.comment
                    for info in before.infolist():
                        after.writestr(copy.copy(info), replacements[info.filename] if info.filename in replacements else before.read(info))
            with zipfile.ZipFile(candidate) as after:
                if after.namelist() != originals or after.comment != before.comment:
                    raise ValueError('JAR layout changed')
                for info in before.infolist():
                    name = info.filename
                    wanted = manifest['replacement'][name] if name in replacements else entry_digest(before, name)
                    if entry_digest(after, name) != wanted:
                        raise ValueError('JAR entry content changed outside the patch: ' + name)
                    old, new = info, after.getinfo(name)
                    for field in ('date_time', 'compress_type', 'external_attr', 'comment', 'extra'):
                        if getattr(old, field) != getattr(new, field):
                            raise ValueError('JAR entry metadata changed: ' + name)
            os.chmod(candidate, original.stat().st_mode & 0o777)
            os.link(candidate, output)  # Atomic, same filesystem, refuses an existing destination.
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    source = commands.add_parser('legacy-source')
    source.add_argument('upstream', type=Path)
    source.add_argument('--apply', action='store_true')
    bundle = commands.add_parser('package')
    bundle.add_argument('classes', type=Path)
    bundle.add_argument('output', type=Path)
    overlay = commands.add_parser('apply')
    overlay.add_argument('original', type=Path)
    overlay.add_argument('patch', type=Path)
    overlay.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'legacy-source':
        changes = legacy_sources(args.upstream)
        if args.apply:
            for original, (destination, text) in changes.items():
                destination.write_text(text, encoding='utf-8')
                if original != destination:
                    original.unlink()
        print(json.dumps({'legacy_source_files': len(changes), 'applied': args.apply}))
    elif args.command == 'package':
        print(json.dumps(package(args.classes, args.output), indent=2))
    else:
        print(json.dumps(apply(args.original, args.patch, args.output), indent=2))
    print('Offline candidates only; no running service, grant or database changed.')


if __name__ == '__main__':
    main()
