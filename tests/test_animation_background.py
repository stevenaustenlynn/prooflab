"""Independent half-pixel oracle for the immutable V6B source metadata."""

import hashlib
import json
import re
import unittest

from prooflab import animation as a

# Derived from accepted cell JSON + fixed glass envelope, not renderer output.
EXPECTED_PIXEL_HASHES = {
    "truecolor/frame-01.ans": "1c719231aeaa8df8c21fe1b2a5684ae6d0e1e45736dffc808ccd2cc27b7bd38c",
    "truecolor/frame-02.ans": "e8cd67fd50d201b17414530324b9a3ff66224c660796cfb89646e5e765a33a2d",
    "truecolor/frame-03.ans": "7a2cb7f155576b8c999349304dd2a8e4204abb3178199f95201a466869ea864e",
    "truecolor/frame-04.ans": "b7ad302f63956974125683cbe4adbdd1342bc7d538fe0e33e2545efa9689cdcb",
    "truecolor/frame-05.ans": "dedf9f48ac8fe18ba85bc4bd1352cc7bbac46642faa42065d593b6e4131622a5",
    "truecolor/frame-06.ans": "452a1b240efca1448f28a4a42ddd3751359918f11371dbb31ad36dc5ca5be5b3",
    "truecolor/frame-07.ans": "2458401728bbaa53bacc9ee211be80b71d3a9fdba987a1ee34bc65c2f25f6fa1",
    "truecolor/frame-08.ans": "9b4c87a9a1572e43225b4ea1da3886448cf3d60ca40e3bdd39e9384656636f92",
    "truecolor/frame-09.ans": "29d4b04c7e6a35cd7bcdbeefdb20eb8866e719f414d144c0ea8e3220bf0045fe",
    "truecolor/frame-10.ans": "0f45ca97883e601c4e676a8e9b15c64778de43a6cf599ebb6f890ca0c1c9e254",
    "truecolor/frame-11.ans": "b989a74afecbeb66755001e55cc8a5969cfe2aba4464b73c5bc2df1216c1c904",
    "truecolor/frame-12.ans": "e152be6644608ee43f7435aeb5ec4a8ade7ceed0d3307b3c994172e3005fa7aa",
    "truecolor/frame-13.ans": "caeef941bffc4ec0e00641791ecb85a3b031ed416169fcd24605956aa10cfdba",
    "truecolor/frame-14.ans": "0b3db1b8e6be02c1bbff9521e6b9581860d648224f878ce466172c96c3f69952",
    "truecolor/frame-15.ans": "11d39d18f792f89d7e9768486b8b21f5bc021248273badfc0546c02fe51d128f",
    "truecolor/frame-16.ans": "46dce1fd847897cf931a9def821b19d57f2f077aa11b0423cb8749af6d811640",
    "truecolor/frame-17.ans": "894c27648e965762d02d2fbab0354eddb12501efff098f55abcc7b1220c33224",
    "truecolor/frame-18.ans": "ce82bf6d085a5217dc3fd0ddf120f3d00fe7cc0659ada435ab7a41ca8e7aba0f",
    "truecolor/frame-19.ans": "72a5c8d468d0b86de0f6a1a939d9840bc687f064cb0613c3ba0b1244d0b551cf",
    "truecolor/frame-20.ans": "c02bb84f37369b95e8175eea68351dfa63dad17f89d66144b8dc58b8de671975",
    "truecolor/frame-21.ans": "d775ab0d608685548c29b17896a86e6b3cbed65bb660c27d47c8e85c9e7c345e",
    "truecolor/frame-22.ans": "66f08500a495333e9ff7d5a4162bd3ee10925bb848eac230985e615dea4cd50f",
    "truecolor/frame-23.ans": "3ffecb58b7390280d797bea462fe3e5a2a4c29338ebfe6c351f088a7025878c8",
    "truecolor/frame-24.ans": "cc6811c7ffa7bedafe9872fdb3552cb1ee72dc88dcefa84169de6735ceb325c5",
    "monochrome/frame-01.ans": "61bc1f5652e09b1ab0cebdb6430e734ae28162d9bbbb6316f519e4911f6a70fe",
    "monochrome/frame-02.ans": "a27e04e80bac430a206cdf7f0d2533681584f20a87665ce7e3baee2e72f89db7",
    "monochrome/frame-03.ans": "63960f5af298f91668a5b53b6da36dd76aafbb9b91c7d66cf194a5ad0cea6ce6",
    "monochrome/frame-04.ans": "cfad4d538d2033b00a0c3070064cd2ade8aa80b75864d3e31791631e610efe4f",
    "monochrome/frame-05.ans": "052b77d1453d1fc1647d3236c8bbe1a29080ed479be8aae32a70c92c8dd50279",
    "monochrome/frame-06.ans": "17eab9ebd3d2dda01b9f0cf87f57b8c3045798f58feacd42f469873199873d71",
    "monochrome/frame-07.ans": "4193838ec1875d3833273e8055f130616649deb6cd82048840b42dab6fb68272",
    "monochrome/frame-08.ans": "7bcbfab9270d6b53411c771b47bbfb16665cd7fa060b039801d32ca622590cc8",
    "monochrome/frame-09.ans": "a96ac7e7843d2b74b2743ef0eed65c4b005507499d4f22b65d1fe3c8bbd4d033",
    "monochrome/frame-10.ans": "9e5fefb6fec4bf90dae7c9609d287c68f89d763d4f552924059eaa25bbc5b3ee",
    "monochrome/frame-11.ans": "a7fddb9dae3b1da841001df88b5d889f7729f8531d5e7ab710194566e7412e1d",
    "monochrome/frame-12.ans": "0f7cc8e479f44cbd54e94fe5df4e576760102a0f83c329246bb61ef6cb1e2db6",
    "monochrome/frame-13.ans": "93006a31649d4c36c04fecb242fd1e5a00e1a08375529838d3925f6c15af3c9f",
    "monochrome/frame-14.ans": "40e86197982d33bfffdbdc6a990e62048431c5b826ab706edb61713fed960854",
    "monochrome/frame-15.ans": "91542b10c4698c631267771da39f2833773a03170f4ce044c0e0ad91197f4a44",
    "monochrome/frame-16.ans": "26184ef761ea11a4d71ad1d4c5e4b93838b77d65941b34f7327dadea28770747",
    "monochrome/frame-17.ans": "1075465fabe1e017fd40587ec5c894aea3198681fd79cd7c757174f1b1a631a9",
    "monochrome/frame-18.ans": "683c8023619db25eb30a476524c49d66c9ba4deb6cc765a4d19a4b4a2cd8c6b4",
    "monochrome/frame-19.ans": "05ef8e62f39e0748e59529346d4d59cbcc722e38ecdd36487d6efffadd899afd",
    "monochrome/frame-20.ans": "ce71f97678763547245c3b00656a6ab841dc8e73a97ed6ec6612a7707cdc92a9",
    "monochrome/frame-21.ans": "f7823dc66139acb9bba1fc412ff2c0ec9b593f78579f87e6e9ddd880b4b032ec",
    "monochrome/frame-22.ans": "5484f7f00047335984e4b36fbb8ca601a92c0f3ded1f4696071a3573e6b95962",
    "monochrome/frame-23.ans": "2dbed879496109f949c677fcd612c291070a9ef1fabb20c8f81b1fe590f6c945",
    "monochrome/frame-24.ans": "da9ac23a1a8d26196231c58f8a63a535e87424caefa1c61615da56059f299886"
}


def pixels(frame):
    """Interpret the emitted SGR/glyph subset independently, with None = default bg."""
    if frame.startswith(a.PREFIX):
        rows = frame[len(a.PREFIX):].split(b'\r\n')
    else:
        rows = frame[len(a.RESTORE):-len(a.RESTORE)].split(b'\x1b[1B\r')
    result = []
    for row in rows:
        foreground = background = None
        halves = [[], []]
        tokens = re.findall(r'\x1b\[([0-9;]+)m|([ ▀▄█])', row.decode())
        for codes, glyph in tokens:
            if codes:
                values = [int(v) for v in codes.split(';')]
                i = 0
                while i < len(values):
                    v = values[i]
                    if v == 0:
                        foreground = background = None
                    elif v in (38, 48):
                        assert values[i+1] == 2
                        color = values[i+2:i+5]
                        if v == 38:
                            foreground = color
                        else:
                            background = color
                        i += 4
                    elif v in (30, 97):
                        foreground = v - 30 if v == 30 else 15
                    elif v in (40, 107):
                        background = v - 40 if v == 40 else 15
                    elif v == 49:
                        background = None
                    else:
                        raise AssertionError(('unexpected SGR', v))
                    i += 1
            else:
                halves[0].append(foreground if glyph in ('▀', '█') else background)
                halves[1].append(foreground if glyph in ('▄', '█') else background)
        assert [len(h) for h in halves] == [32, 32]
        result.extend(halves)
    assert len(result) == 72
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


class BackgroundTests(unittest.TestCase):
    def test_all_half_pixels_match_accepted_metadata_oracle(self):
        for mode in ('truecolor', 'monochrome'):
            for index, frame in enumerate(a.load_frames(mode), 1):
                name = f'{mode}/frame-{index:02d}.ans'
                self.assertEqual(digest(pixels(frame)), EXPECTED_PIXEL_HASHES[name], name)

    def test_all_intentional_colors_unchanged(self):
        for mode in ('truecolor', 'monochrome'):
            matte = [2, 11, 8] if mode == 'truecolor' else 0
            protected = 0
            for index, frame in enumerate(a.load_frames(mode), 1):
                raw = a.files('prooflab').joinpath('animation_frames', mode, f'frame-{index:02d}.ans').read_bytes()
                before, after = pixels(raw), pixels(frame)
                for y in range(72):
                    for x in range(32):
                        if before[y][x] != matte:
                            self.assertEqual(after[y][x], before[y][x])
                            protected += 1
                        if after[y][x] is None:
                            self.assertEqual(before[y][x], matte)
            self.assertGreater(protected, 1000)

    def test_interior_negative_space_stays_dark(self):
        # Source geometry's two open inner channels and dark cavity are deliberate.
        for mode in ('truecolor', 'monochrome'):
            for i, frame in enumerate(a.load_frames(mode), 1):
                raw = a.files('prooflab').joinpath('animation_frames', mode, f'frame-{i:02d}.ans').read_bytes()
                before, after = pixels(raw), pixels(frame)
                for y in range(21, 62):
                    for x in (8, 23):
                        self.assertEqual(after[y][x], before[y][x])
                        self.assertIsNotNone(after[y][x])

    def test_theme_background_not_an_explicit_rectangle(self):
        for theme in ([0, 0, 0], [42, 49, 66], [238, 226, 204]):
            for mode in ('truecolor', 'monochrome'):
                for frame in a.load_frames(mode):
                    raster = pixels(frame)
                    for x in range(32):
                        self.assertIsNone(raster[-1][x])
                    for row in raster:
                        self.assertIsNone(row[0])
                        self.assertIsNone(row[-1])
                    themed = [[theme if p is None else p for p in row] for row in raster]
                    self.assertEqual(themed[0][0], theme)
                    self.assertEqual(themed[0][-1], theme)
                    self.assertEqual(themed[-1], [theme] * 32)
                    self.assertIn(b'\x1b[49m ', frame)

    def test_moving_effects_are_actively_erased_including_wrap(self):
        for mode in ('truecolor', 'monochrome'):
            frames = [pixels(f) for f in a.load_frames(mode)]
            erased = 0
            for before, after in zip(frames, frames[1:] + frames[:1]):
                # Every frame contains all 32x72 halves, including default-backed
                # spaces/half blocks at positions that were previously particles.
                erased += sum(before[y][x] is not None and after[y][x] is None
                              for y in range(72) for x in range(32))
            self.assertGreater(erased, 0)

    def test_half_block_orientation_and_protected_both_colored(self):
        def render(parameters, glyph, x, y):
            original = '\x1b[' + parameters + 'm' + glyph
            return a._sprite_cell(a.CELL.fullmatch(original), x, y, 'truecolor')
        fg = '38;2;113;237;177'
        self.assertEqual(render(fg + ';48;2;2;11;8', '▀', 0, 0), '\x1b[' + fg + ';49m▀')
        self.assertEqual(render(fg + ';48;2;2;11;8', '▄', 0, 0), '\x1b[' + fg + ';49m▄')
        both = fg + ';48;2;5;43;35'
        self.assertEqual(render(both, '▀', 15, 20), '\x1b[' + both + 'm▀')
        empty = '38;2;2;11;8;48;2;2;11;8'
        self.assertEqual(render(empty, ' ', 0, 0), '\x1b[49m ')
        self.assertEqual(render(empty, ' ', 15, 20), '\x1b[' + empty + 'm ')

    def test_malformed_cell_colors_fail_closed(self):
        for parameters in ('38;2;999;0;0;48;2;0;0;0', '38;5;2;48;5;2'):
            match = a.CELL.fullmatch('\x1b[' + parameters + 'm ')
            with self.assertRaises(ValueError):
                a._sprite_cell(match, 0, 0, 'truecolor')
        with self.assertRaises(ValueError):
            a._sprite_cell(a.CELL.fullmatch('\x1b[31;41m '), 0, 0, 'monochrome')


if __name__ == '__main__':
    unittest.main()
