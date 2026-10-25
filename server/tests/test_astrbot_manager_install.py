"""Synthetic archive preservation/failure tests; actual legacy Java runtime is tested by act."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from server.companion import install_astrbot_manager as installer


class LegacyManagerCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original, self.patch, self.output = [self.root / name for name in ('original.jar', 'patch.zip', 'candidate.jar')]
        self.old = {name: b'original-class-fixture-' + name.encode() for name in installer.PREIMAGES}
        self.expected = {name: hashlib.sha256(data).hexdigest() for name, data in self.old.items()}
        self.classes = self.root / 'classes'
        for name in installer.PREIMAGES:
            path = self.classes / name.removeprefix('BOOT-INF/classes/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\xca\xfe\xba\xbe\0\0\0A' + name.encode())
        # Preserve executable prefix, nested stored JAR, unrelated config and metadata.
        with self.original.open('wb') as stream:
            stream.write(b'#!/bin/sh\n# synthetic launch prefix\n')
            with zipfile.ZipFile(stream, 'w') as jar:
                jar.comment = b'original archive comment'
                for name, data in self.old.items():
                    jar.writestr(name, data)
                info = zipfile.ZipInfo('BOOT-INF/classes/application.yml', date_time=(2020, 1, 2, 3, 4, 6))
                info.comment, info.external_attr = b'preserve metadata', 0o100640 << 16
                jar.writestr(info, b'fixture configuration, not credentials')
                jar.writestr('BOOT-INF/lib/unchanged.jar', b'fixture library', compress_type=zipfile.ZIP_STORED)

    def tearDown(self):
        self.temporary.cleanup()

    def test_legacy_patch_preserves_every_other_entry_and_never_overwrites_original(self):
        before = self.original.read_bytes()
        with patch.object(installer, 'PREIMAGES', self.expected):
            manifest = installer.package(self.classes, self.patch)
            installer.apply(self.original, self.patch, self.output)
        self.assertEqual(self.original.read_bytes(), before)
        self.assertTrue(self.output.read_bytes().startswith(b'#!/bin/sh\n'))
        with zipfile.ZipFile(self.original) as old, zipfile.ZipFile(self.output) as new:
            self.assertEqual(old.namelist(), new.namelist())
            self.assertEqual(old.comment, new.comment)
            for name in old.namelist():
                expected = manifest['replacement'].get(name, hashlib.sha256(old.read(name)).hexdigest())
                self.assertEqual(hashlib.sha256(new.read(name)).hexdigest(), expected)
                for field in ('date_time', 'compress_type', 'external_attr', 'comment', 'extra'):
                    self.assertEqual(getattr(old.getinfo(name), field), getattr(new.getinfo(name), field))
        with self.assertRaisesRegex(ValueError, 'new candidate'):
            installer.apply(self.original, self.patch, self.original)
        self.output.write_bytes(b'preserve existing output')
        with self.assertRaisesRegex(ValueError, 'new candidate'):
            installer.apply(self.original, self.patch, self.output)
        self.assertEqual(self.output.read_bytes(), b'preserve existing output')

    def test_unreviewed_preimage_extra_entries_and_tampered_payload_are_rejected(self):
        original = self.original.read_bytes()
        with patch.object(installer, 'PREIMAGES', self.expected):
            installer.package(self.classes, self.patch)
        with self.assertRaisesRegex(ValueError, 'Unreviewed patch'):
            installer.apply(self.original, self.patch, self.output)
        with zipfile.ZipFile(self.patch) as source:
            contents = {name: source.read(name) for name in source.namelist()}
        for kind in ('extra', 'checksum', 'preimage'):
            altered = self.root / (kind + '.zip')
            items = dict(contents)
            if kind == 'extra': items['BOOT-INF/classes/unrelated.class'] = b'not allowed'
            elif kind == 'checksum': items[next(iter(self.expected))] += b'tampered'
            else:
                info = json.loads(items['manifest.json'])
                info['expected'] = dict(self.expected, **{next(iter(self.expected)): '0' * 64})
                items['manifest.json'] = json.dumps(info).encode()
            with zipfile.ZipFile(altered, 'w') as target:
                for name, data in items.items(): target.writestr(name, data)
            with patch.object(installer, 'PREIMAGES', self.expected), self.assertRaises(ValueError):
                installer.apply(self.original, altered, self.output)
            self.assertFalse(self.output.exists())
        self.assertEqual(self.original.read_bytes(), original)

    def test_legacy_source_conversion_changes_only_two_type_names_and_refuses_drift(self):
        base = self.root / 'main/manager-api/src/main/java/xiaozhi/modules/device'
        sources = Path(installer.__file__).with_name('manager_identity')
        for name, folder in (('DeviceAuthorizationController', 'controller'), ('DeviceAuthorizationService', 'service/impl')):
            target = base / folder / ('Youmi' + name + '.java')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((sources / target.name).read_bytes())
        extra = base / 'Unrelated.java'
        extra.write_text('class Unrelated {}')
        changes = installer.legacy_sources(self.root)
        self.assertEqual(len(changes), 2)
        for original, (destination, text) in changes.items():
            self.assertEqual(text, original.read_text().replace('YoumiDeviceAuthorization', 'MiaDeviceAuthorization'))
            destination.write_text(text)
            original.unlink()
        self.assertEqual(extra.read_text(), 'class Unrelated {}')
        with self.assertRaisesRegex(ValueError, 'Unreviewed or already converted'):
            installer.legacy_sources(self.root)
