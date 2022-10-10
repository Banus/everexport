"""Converts Evernote HTML files to Markdown using Beautiful Soup."""
import argparse
import codecs
import logging
import json
import os
import re

from itertools import chain
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString


_MONOSPACE_FONTS = ["Andale Mono", "Consolas", "Courier New", "Lucida Console"]
COLOR_INTERNAL_LINKS = True
INLINE_PREVIEWS = True


def parse_span_style(tokens):
    """Parse the style of span elements."""
    style = {}
    style['italic'] = tokens.get('font-style', None) == 'italic'
    style['bold'] = tokens.get('font-weight', None) == 'bold'
    style['underline'] = tokens.get('text-decoration', None) == 'underline'
    style['sub'] = tokens.get('vertical-align', None) == 'sub'
    style['sup'] = tokens.get('vertical-align', None) == 'super'
    style['code'] = "courier" in tokens.get('font-family', "").lower()
    style['color'] = tokens.get('color', False)

    return {k: v for k, v in style.items() if v}  # drop useless attributes


def _parse_div_style(tokens):
    return {'codeblock': tokens.get("-en-codeblock", "") == "true"}


def _parse_font_style(tokens):
    return {'code': "courier" in tokens.get('font-family', "").lower()}


def _parse_img(tag):
    """Parse img tags."""
    name = tag.get('data-filename', "")[:-4] or tag.get('alt', "")
    name = quote(os.path.splitext(name)[0])
    attr = [f" alt=\"{name}\"" if name else ""]
    attr += [f" width={tag.get('width')}" if 'width' in tag.attrs else ""]
    attr = ''.join(attr)

    return f"<img src=\"{tag.get('src')}\"{attr}>"


def _escape(string):
    """Escape reserved Markdown characters."""
    return string.replace("<", r"\<").replace(">", r"\>")

def _unescape(string):
    """Unescape reserved Markdown characters for codeblocks."""
    return string.replace(r"\>", ">").replace(r"\<", "<")


def decode_style(prop, parser=parse_span_style):
    """Decode style-specific attributes."""
    if not prop:
        return {}

    # occasionally, Evernote drops the font-family attribute
    prop = re.sub("\";} \"", "\"Courier New\"", prop)
    tokens = [t.strip() for t in prop.split(';') if t]
    tokens = dict([tuple(x.strip() for x in t.split(':')) for t in tokens])
    # handle aliases
    tokens['text-decoration'] = tokens.pop('text-decoration-line',
                                           tokens.get('text-decoration', None))

    return parser(tokens)


def _format(string, attr, warn=True):
    """Add formats to string; beware of the order."""
    if not attr or not string.strip():
        return string

    if attr.get('color', "") == "rgb(0, 0, 0)":
        attr.pop('color')  # remove default color

    if attr.get('code', False):
        # spaces are non-breaking in code; remove leading/trailing spaces
        string = _unescape(string.replace(chr(160), " ").strip())
        if not string:  # nothing left
            if warn:
                logging.warning("Empty code detected")
                return "<empty code>"
            return ""
        return f"`{string}`"  # no further formatting is possible
    
    or_str = string
    if attr.get('sup', False):
        string = f"<sup>{string}</sup>"
    if attr.get('sub', False):
        string = f"<sub>{string}</sub>"
    if attr.get('underline', False):
        string = f"<u>{string}</u>"
    if attr.get('color', False):
        string = f"<span style=\"color: {attr['color']}\">{string}</span>"
    if attr.get('italic', False):
        string = f"*{string}*"
    if attr.get('bold', False):
        string = f"**{string}**"
    if attr.get('link', None):
        if attr.get('italic', False) and not attr.get('underline', False):
            logging.warning(f"Entry not underlined: {or_str}")
        string = f"[{string}]({attr['link']})"

    return string


def _replace_cr_except_code(txt, langs, cr_):
    """Replace single newlines except for bullet lists and code blocks."""
    codeblocks = list(re.finditer(r"```bash([\S|\s]+?)```", txt))
    if langs and len(codeblocks) != len(langs):
        raise ValueError("Wrong number of code block hints.")

    steps = [0] + list(chain(*[c.span() for c in codeblocks])) + [len(txt)]
    segs = [txt[s:e] for s, e in zip(steps[:-1], steps[1:])]

    for idx, seg in enumerate(segs):
        if idx % 2 == 0:
            segs[idx] = re.sub(r"(\S)\n([^\n-])", rf"\1{cr_}\n\2", seg)
        elif langs:
            lang = langs[idx // 2]
            if lang != "bash":
                segs[idx] = re.sub("```bash", f"```{lang}", seg)

    return ''.join(segs)


def finalize(txt, hints, cr_="<br>"):
    """Clean up residual formatting."""
    # fix special characters
    txt = txt.replace(chr(160), "&nbsp;")
    txt = txt.replace(codecs.BOM_UTF8.decode('utf-8'), "")

    # remove multiple empty lines
    txt = re.sub(r"\n\n\n+", "\n\n", txt)
    # remove lone nbsp - needed before single cr conversion
    txt = re.sub(r"\n\**(&nbsp;)+\**\n", "\n\n", txt)
    # # nbsp not at line start are spaces
    # txt = re.sub(r"([^\n])(&nbsp;)", r"\1 ", txt)
    # remove trailing spaces
    txt = re.sub(r" *\n", "\n", txt)
    txt = _replace_cr_except_code(txt, hints.get('codeblocks', []), cr_)

    return txt.strip() + '\n'


class HTMLParser():
    """Convert HTML to Markdown."""

    def __init__(self, cr_=r"\\", hints=None):
        """Initialize parser for a single document."""
        self.cr_ = cr_
        self.hints = hints or {}
        self.internal_links = []

    def parse(self, html):
        """Convert HTML to Markdown."""
        with open(html, 'r', encoding='utf-8-sig') as fid:
            soup = BeautifulSoup(fid, 'html.parser')
        
        tag = next(soup.children)  # gwt HTML tag
        tag = next((el for el in tag.children if el.name == 'body'), None)
        if tag is None:
            raise ValueError("No content found")

        txt, _ = self.process_tag(tag)
        return finalize(txt, self.hints, self.cr_)

    def _parse_link(self, tag, txt, style):
        """Format a link."""
        link = tag.get('href', '')
        if not link or link == "https:":
            logging.warning(f"Empty link found for {tag.text}")
            return tag.text, {}
        if link == tag.text:
            return tag.text, {}  # use autolink

        style = decode_style(tag.get('style', ''))
        inter = style.get('color', False) in ("rgb(105, 170, 53)", "#69aa35")

        if '://' not in link:  # internal link
            if link.endswith('.html'):
                link = link.replace(".html", ".md")
                self.internal_links.append((tag.text, link))
            elif link.endswith(('.mp4', '.pdf')) and INLINE_PREVIEWS:
                # discard preview image and link file directly
                txt = os.path.basename(link)

            if not COLOR_INTERNAL_LINKS and inter:
                # remove color for internal links
                style.pop('color')
            link = quote(link)

        style['link'] = link
        return txt, style
    
    def _merge(self, children):
        """Merge text from children."""
        if all(c.name == 'span' for c in children):
            return self._merge_spans(children)

        txt, chunks = "", []
        for tag in children:
            old_txt = txt
            txt, style = self.process_tag(tag)
            if tag.name == 'div' and not old_txt.endswith('\n'):
                txt = '\n' + txt
            chunks.append(_format(txt, style))
        return ''.join(chunks)
    
    def _merge_spans(self, children):
        """Merge text from children."""
        txts, styles = zip(*[self.process_tag(c) for c in children])
        if all(s == styles[0] for s in styles):
            return _format(''.join(txts), styles[0])
        return ''.join(_format(t, s) for t, s in zip(txts, styles))

    def process_tag(self, tag):
        """Process a tag."""
        if isinstance(tag, NavigableString):
            return _escape(str(tag)), {}

        style, txt, children = {}, tag.text, list(tag.children)
        if len(children) > 1:
            txt = self._merge(children)
        elif len(children) == 1:
            txt, style = self.process_tag(children[0])
            if tag.name != 'span':  # stop here
                txt, style = _format(txt, style), {}

        if tag.name == 'div':
            div_style = decode_style(tag.get('style', ''), _parse_div_style)
            if div_style.get('codeblock', False):
                txt = f"\n```bash\n{txt}\n```"
        elif tag.name == 'span':
            if len(children) <= 1:
                # if multiple children, their style overrides the parent
                style.update(decode_style(tag.get('style', '')))
        elif tag.name == 'h1':
            txt = f"# {txt}"
        elif tag.name == 'a':
            txt, style = self._parse_link(tag, txt, style)
        elif tag.name == 'img':
            txt = _parse_img(tag)
        elif tag.name == 'font':
            font = tag.get('face', "")
            style['code'] = font in _MONOSPACE_FONTS or decode_style(
                tag.get('style', ""), _parse_font_style)
            if font and font not in _MONOSPACE_FONTS:
                logging.warning(f"Unknown font: {font}")
        elif tag.name == 'p':
            txt = f"{txt}\n"
        elif tag.name == 'br':
            txt = '\n'
        elif tag.name == 'ul':
            txt = f"\n{txt}"
        elif tag.name == 'li':
            txt = f"- {txt}\n"
        elif tag.name == 'u':
            style['underline'] = True
        elif tag.name in ('i', 'em'):
            style['italic'] = True
        elif tag.name in ('b', 'strong'):
            style['bold'] = True
        elif tag.name == 'sup':
            style['sup'] = True
        elif tag.name == 'sub':
            style['sub'] = True
        elif tag.name == 'body':
            pass   # do noting more
        else:
            logging.warning(f"Unknown tag: {tag.name}")
        return txt, style


def convert_html_to_markdown(html, hints):
    """Convert HTML to Markdown."""
    parser = HTMLParser(hints=hints)
    txt = parser.parse(html)

    return txt, parser.internal_links


def main():
    """Script entry point."""
    parser = argparse.ArgumentParser(
        description='Convert Evernote HTML to Markdown')

    parser.add_argument("path", type=str, help="file to convert")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="recursively convert all files following links.")
    args = parser.parse_args()

    with open("hints.json", 'r') as fid:
        hints = json.load(fid)

    links, external, converted = [os.path.split(args.path)], [], []
    while links:
        path, fname = links.pop()
        logging.info(f"Converting {fname}")

        out_name, _ = os.path.splitext(fname)
        txt, curr_links = convert_html_to_markdown(
            os.path.join(path, fname), hints['files'].get(fname, {}))

        out_file = os.path.join(path, f"{out_name}.md")
        with open(out_file, 'w', encoding="utf-8") as fid:
            fid.write(txt)
        converted.append(fname)

        if args.recursive:
            links += [(path, f) for _, f in curr_links
                      if not f.startswith('evernote')]
            external += [(lbl, f) for lbl, f in curr_links
                         if f.startswith('evernote')]

    if external:
        print("\nExternal links:")
        for lbl, fname in external:
            print(f"{lbl}: {fname}")
    if args.recursive:
        all_files = [f for f in os.listdir(path) if f.endswith('.html')]
        print("\nMissing files:")
        for fname in set(all_files) - set(converted):
            print(f"{fname}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
