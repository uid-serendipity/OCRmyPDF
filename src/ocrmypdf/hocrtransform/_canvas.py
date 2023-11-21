# SPDX-FileCopyrightText: 2023 James R. Barlow
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import logging
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files as package_files
from pathlib import Path

from pikepdf import (
    ContentStreamInstruction,
    Dictionary,
    Matrix,
    Name,
    Operator,
    Pdf,
    unparse_content_stream,
)
from PIL import Image

log = logging.getLogger(__name__)

GLYPHLESS_FONT_NAME = 'pdf.ttf'

GLYPHLESS_FONT = (package_files('ocrmypdf.data') / GLYPHLESS_FONT_NAME).read_bytes()
CHAR_ASPECT = 2


class TextDirection(Enum):
    LTR = ...
    RTL = ...


def register_glyphlessfont(pdf: Pdf):
    """Register the glyphless font.

    Create several data structures in the Pdf to describe the font. While it create
    the data, a reference should be set in at least one page's /Resources dictionary
    to retain the font in the output PDF and ensure it is usable on that page.
    """
    PLACEHOLDER = Name.Placeholder

    basefont = pdf.make_indirect(
        Dictionary(
            BaseFont=Name.GlyphLessFont,
            DescendantFonts=[PLACEHOLDER],
            Encoding=Name("/Identity-H"),
            Subtype=Name.Type0,
            ToUnicode=PLACEHOLDER,
            Type=Name.Font,
        )
    )
    cid_font_type2 = pdf.make_indirect(
        Dictionary(
            BaseFont=Name.GlyphLessFont,
            CIDToGIDMap=PLACEHOLDER,
            CIDSystemInfo=Dictionary(
                Ordering="Identity",
                Registry="Adobe",
                Supplement=0,
            ),
            FontDescriptor=PLACEHOLDER,
            Subtype=Name.CIDFontType2,
            Type=Name.Font,
            DW=1000 // CHAR_ASPECT,
        )
    )
    basefont.DescendantFonts = [cid_font_type2]
    cid_font_type2.CIDToGIDMap = pdf.make_stream(b"\x00\x01" * 65536)
    basefont.ToUnicode = pdf.make_stream(
        b"/CIDInit /ProcSet findresource begin\n"
        b"12 dict begin\n"
        b"begincmap\n"
        b"/CIDSystemInfo\n"
        b"<<\n"
        b"  /Registry (Adobe)\n"
        b"  /Ordering (UCS)\n"
        b"  /Supplement 0\n"
        b">> def\n"
        b"/CMapName /Adobe-Identify-UCS def\n"
        b"/CMapType 2 def\n"
        b"1 begincodespacerange\n"
        b"<0000> <FFFF>\n"
        b"endcodespacerange\n"
        b"1 beginbfrange\n"
        b"<0000> <FFFF> <0000>\n"
        b"endbfrange\n"
        b"endcmap\n"
        b"CMapName currentdict /CMap defineresource pop\n"
        b"end\n"
        b"end\n"
    )
    font_descriptor = pdf.make_indirect(
        Dictionary(
            Ascent=1000,
            CapHeight=1000,
            Descent=-1,
            Flags=5,  # Fixed pitch and symbolic
            FontBBox=[0, 0, 1000 // CHAR_ASPECT, 1000],
            FontFile2=PLACEHOLDER,
            FontName=Name.GlyphLessFont,
            ItalicAngle=0,
            StemV=80,
            Type=Name.FontDescriptor,
        )
    )
    font_descriptor.FontFile2 = pdf.make_stream(GLYPHLESS_FONT)
    cid_font_type2.FontDescriptor = font_descriptor
    return basefont


class ContentStreamBuilder:
    def __init__(self, instructions=None):
        self._instructions: list[ContentStreamInstruction] = instructions or []

    def push(self):
        """Save the graphics state."""
        inst = ContentStreamInstruction([], Operator("q"))
        self._instructions.append(inst)
        return self

    def pop(self):
        """Restore the graphics state."""
        inst = ContentStreamInstruction([], Operator("Q"))
        self._instructions.append(inst)
        return self

    def cm(self, matrix: Matrix):
        """Concatenate matrix."""
        inst = ContentStreamInstruction(matrix.shorthand, Operator("cm"))
        self._instructions.append(inst)
        return self

    def begin_text(self):
        """Begin text object."""
        inst = ContentStreamInstruction([], Operator("BT"))
        self._instructions.append(inst)
        return self

    def end_text(self):
        """End text object."""
        inst = ContentStreamInstruction([], Operator("ET"))
        self._instructions.append(inst)
        return self

    def begin_marked_content_proplist(self, mctype: Name, mcid: int):
        """Begin marked content sequence."""
        inst = ContentStreamInstruction(
            [mctype, Dictionary(MCID=mcid)], Operator("BDC")
        )
        self._instructions.append(inst)
        return self

    def begin_marked_content(self, mctype: Name):
        """Begin marked content sequence."""
        inst = ContentStreamInstruction([mctype], Operator("BMC"))
        self._instructions.append(inst)
        return self

    def end_marked_content(self):
        """End marked content sequence."""
        inst = ContentStreamInstruction([], Operator("EMC"))
        self._instructions.append(inst)
        return self

    def set_text_font(self, font: Name, size: int):
        """Set text font and size."""
        inst = ContentStreamInstruction([font, size], Operator("Tf"))
        self._instructions.append(inst)
        return self

    def set_text_matrix(self, matrix: Matrix):
        """Set text matrix."""
        inst = ContentStreamInstruction(matrix.shorthand, Operator("Tm"))
        self._instructions.append(inst)
        return self

    def set_text_rendering(self, mode: int):
        """Set text rendering mode."""
        inst = ContentStreamInstruction([mode], Operator("Tr"))
        self._instructions.append(inst)
        return self

    def set_text_horizontal_scaling(self, scale: float):
        """Set text horizontal scaling."""
        inst = ContentStreamInstruction([scale], Operator("Tz"))
        self._instructions.append(inst)
        return self

    def show_text(self, text: str):
        """Show text."""
        encoded = text.encode("utf-16be")
        inst = ContentStreamInstruction([[encoded]], Operator("TJ"))
        self._instructions.append(inst)
        return self

    def move_cursor(self, dx, dy):
        """Move cursor."""
        inst = ContentStreamInstruction([dx, dy], Operator("Td"))
        self._instructions.append(inst)
        return self

    def stroke_and_close(self):
        """Stroke and close path."""
        inst = ContentStreamInstruction([], Operator("s"))
        self._instructions.append(inst)
        return self

    def fill(self):
        """Stroke and close path."""
        inst = ContentStreamInstruction([], Operator("f"))
        self._instructions.append(inst)
        return self

    def append_rectangle(self, x: float, y: float, w: float, h: float):
        """Append rectangle to path."""
        inst = ContentStreamInstruction([x, y, w, h], Operator("re"))
        self._instructions.append(inst)
        return self

    def set_stroke_color(self, r: float, g: float, b: float):
        """Set RGB stroke color."""
        inst = ContentStreamInstruction([r, g, b], Operator("RG"))
        self._instructions.append(inst)
        return self

    def set_fill_color(self, r: float, g: float, b: float):
        """Set RGB fill color."""
        inst = ContentStreamInstruction([r, g, b], Operator("rg"))
        self._instructions.append(inst)
        return self

    def set_line_width(self, width):
        """Set line width."""
        inst = ContentStreamInstruction([width], Operator("w"))
        self._instructions.append(inst)
        return self

    def line(self, x1: float, y1: float, x2: float, y2: float):
        """Draw line."""
        insts = [
            ContentStreamInstruction([x1, y1], Operator("m")),
            ContentStreamInstruction([x2, y2], Operator("l")),
        ]
        self._instructions.extend(insts)
        return self

    def set_dashes(self, array=None, phase=0):
        """Set dashes."""
        if array is None:
            array = []
        if isinstance(array, (int, float)):
            array = (array, phase)
            phase = 0
        inst = ContentStreamInstruction([array, phase], Operator("d"))
        self._instructions.append(inst)
        return self

    def draw_form_xobject(self, name: Name):
        inst = ContentStreamInstruction([name], Operator("Do"))
        self._instructions.append(inst)
        return self

    def build(self):
        return self._instructions


@dataclass
class LoadedImage:
    name: Name
    image: Image.Image


class PikepdfCanvasAccessor:
    def __init__(self, cs: ContentStreamBuilder, images=None):
        self._cs = cs
        self._images = images if images is not None else []
        self._stack_depth = 0

    def stroke_color(self, color):
        r, g, b = color.red, color.green, color.blue
        self._cs.set_stroke_color(r, g, b)
        return self

    def fill_color(self, color):
        r, g, b = color.red, color.green, color.blue
        self._cs.set_fill_color(r, g, b)
        return self

    def line_width(self, width):
        self._cs.set_line_width(width)
        return self

    def line(self, x1, y1, x2, y2):
        self._cs.line(x1, y1, x2, y2)
        self._cs.stroke_and_close()
        return self

    def rect(self, x, y, w, h, fill):
        self._cs.append_rectangle(x, y, w, h)
        if fill:
            self._cs.fill()
        else:
            self._cs.stroke_and_close()
        return self

    def draw_image(self, image: Path | str | Image.Image, x, y, width, height):
        with self.enter_context():
            self.cm(Matrix(width, 0, 0, height, x, y))
            if isinstance(image, (Path, str)):
                image = Image.open(image)
            image.load()
            if image.mode == "P":
                image = image.convert("RGB")
            if image.mode not in ("1", "L", "RGB"):
                raise ValueError(f"Unsupported image mode: {image.mode}")
            name = Name.random(prefix="Im")
            li = LoadedImage(name, image)
            self._images.append(li)
            self._cs.draw_form_xobject(name)

    def draw_text(self, text: PikepdfText):
        self._cs._instructions.extend(text._cs.build())
        self._end_text()

    def _end_text(self):
        self._cs.end_text()

    def dashes(self, *args):
        self._cs.set_dashes(*args)
        return self

    def push(self):
        self._cs.push()
        self._stack_depth += 1
        return self

    def pop(self):
        self._cs.pop()
        self._stack_depth -= 1
        return self

    @contextmanager
    def enter_context(self):
        """Save the graphics state and restore it on exit."""
        self.push()
        yield self
        self.pop()

    def cm(self, matrix):
        self._cs.cm(matrix)
        return self


class PikepdfCanvas:
    def __init__(self, *, page_size: tuple[int | float, int | float]):
        self.page_size = page_size
        self._pdf = Pdf.new()
        self._page = self._pdf.add_blank_page(page_size=page_size)
        self._cs = ContentStreamBuilder()
        self._images: list[LoadedImage] = []
        self._accessor = PikepdfCanvasAccessor(self._cs, self._images)
        self._stack_depth = 0
        self._font_name = Name("/f-0-0")
        self.do.push()

    @property
    def do(self) -> PikepdfCanvasAccessor:
        return self._accessor

    def string_width(self, text, fontname, fontsize):
        # NFKC: split ligatures, combine diacritics
        return len(unicodedata.normalize("NFKC", text)) * (fontsize / CHAR_ASPECT)

    def _save_image(self, li: LoadedImage):
        return self._pdf.make_stream(
            li.image.tobytes(),
            Width=li.image.width,
            Height=li.image.height,
            ColorSpace=Name.DeviceGray
            if li.image.mode in ("1", "L")
            else Name.DeviceRGB,
            Type=Name.XObject,
            Subtype=Name.Image,
            BitsPerComponent=1 if li.image.mode == '1' else 8,
        )

    def save(self, output_file: Path):
        self.do.pop()
        if self._stack_depth != 0:
            log.warning(
                "Graphics state stack is not empty when page saved - "
                "rendering may be incorrect"
            )
        self._page.Contents = self._pdf.make_stream(
            unparse_content_stream(self._cs.build())
        )
        self._page.MediaBox = [0, 0, *self.page_size]
        self._page.Resources = Dictionary(Font=Dictionary(), XObject=Dictionary())
        self._page.Resources.Font[self._font_name] = register_glyphlessfont(self._pdf)
        for li in self._images:
            self._page.Resources.XObject[li.name] = self._save_image(li)
        self._pdf.save(output_file)


class PikepdfText:
    def __init__(self, x=0, y=0, direction=TextDirection.LTR):
        self._cs = ContentStreamBuilder()
        self._cs.begin_text()
        self._p0 = (x, y)
        self._direction = direction

    def set_font(self, font, size):
        self._cs.set_text_font(Name("/f-0-0"), size)
        return self

    def set_render_mode(self, mode):
        self._cs.set_text_rendering(mode)
        return self

    def set_text_transform(self, matrix: Matrix):
        self._cs.set_text_matrix(matrix)
        self._p0 = (matrix.e, matrix.f)
        return self

    def show(self, text: str):
        if self._direction == TextDirection.LTR:
            self._cs.show_text(text)
        else:
            self._cs.begin_marked_content(Name.ReversedChars)
            self._cs.show_text(text)
            self._cs.end_marked_content()
        return self

    def set_horiz_scale(self, scale):
        self._cs.set_text_horizontal_scaling(scale)
        return self

    def get_start_of_line(self):
        return self._p0

    def move_cursor(self, x, y):
        self._cs.move_cursor(x, y)
        return self
