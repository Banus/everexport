"""Converts Evernote HTML files to Markdown."""
import argparse
import codecs
import logging
import json
import os
import re

from itertools import chain
from html.parser import HTMLParser
from urllib.parse import quote, unquote

_IGNORED_TAGS = ['html', 'head', 'title', 'basefont', 'meta', 'style']
_MONOSPACE_FONTS = ["Andale Mono", "Consolas", "Courier New", "Lucida Console"]


def parse_span_style(tokens):
    """Parse the style of span elements."""
    style = {}
    style['italic'] = tokens.get('font-style', None) == 'italic'
    style['bold'] = tokens.get('font-weight', None) == 'bold'
    style['underline'] = tokens.get('text-decoration', None) == 'underline'
    style['sub'] = tokens.get('vertical-align', None) == 'sub'
    style['code'] = "courier" in tokens.get('font-family', "").lower()
    style['color'] = tokens.get('color', False)

    return style


def _parse_div_style(tokens):
    return {'codeblock': tokens.get("-en-codeblock", "") == "true"}


def _parse_font_style(tokens):
    return {'code': "courier" in tokens.get('font-family', "").lower()}


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
    if not attr:
        return string

    # flatten attributes
    attr_flat = {k: v for k, v in attr.items() if k not in ('a', 'span')}
    attr_span = attr.get('span', [])
    attr_span = attr_span[-1] if attr_span else {}
    attr = {**attr.get('a', {}), **attr_span, **attr_flat}

    or_str = string
    string = string.replace("<", r"\<").replace(">", r"\>")  # sanitize

    if attr.get('color', "") == "rgb(0, 0, 0)":
        attr.pop('color')  # remove default color

    if attr.get('code', False):
        # spaces are non-breaking in code; remove leading/trailing spaces
        string = string.replace(chr(160), " ").strip()
        if not string:  # nothing left
            if warn:
                logging.warning("Empty code detected")
                return "<empty code>"
            return ""
        return f"`{string}`"  # no further formatting is possible

    if attr.get('img', False):
        string = attr['img']
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
        if attr['link'] == "https:":
            logging.warning(f"Empty link found for {or_str}")
        if attr.get('italic', False) and not attr.get('underline', False):
            logging.warning(f"Entry not underlined: {or_str}")
        string = f"[{string}]({attr['link']})"

    return string


def _parse_img(state, attrs):
    """Parse img tags."""
    link = state.get('a', {}).get('link', "")
    if link.endswith((".png", ".mp4")):
        # preview image for MP4/PDF, leave only link to file
        #  otherwise, keep the preview and link to generic file
        return _format(attrs['alt'], state)

    name = attrs.get('data-filename', "")[:-4] or attrs.get('alt', "")
    name = quote(os.path.splitext(name)[0])
    attr = [f" alt=\"{name}\"" if name else ""]
    attr += [f" width={attrs['width']}" if 'width' in attrs else ""]
    attr = ''.join(attr)

    txt = f"<img src=\"{quote(attrs['src'])}\"{attr}>"
    if link:
        txt = f"[{txt}]({link})"
    return txt


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


# check: https://docs.python.org/3/library/html.parser.html
class HTMLMDParser(HTMLParser):
    """Convert each HTML tag in a corresponding Markdown construct."""

    def __init__(self):
        """Initialize parser for a single document."""
        super(HTMLMDParser, self).__init__()
        self.tags, self.last, self.div_lvl = [], None, 0
        self.attr, self.running, self.txt = {}, False, ""
        self.internal_links = []

    def handle_starttag(self, tag, attrs):
        """Handle state update for a new tag."""
        self.tags.append(tag)
        if tag in _IGNORED_TAGS:
            return
        attrs = dict(attrs)

        if tag == 'span':
            if self.txt[-1] in ('*', '_'):
                # hack: avoid bad syntax with two close spans
                self.txt += " "
            if 'style' in attrs:
                self.attr['span'] = self.attr.get('span', []) + [
                    decode_style(attrs['style'])]
        elif tag == 'div':
            if 'style' in attrs:
                attr = decode_style(attrs['style'], _parse_div_style)
                if attr.get('codeblock', False):
                    self.div_lvl = len(self.tags)
                    self.txt += "\n```bash\n"
        elif tag == 'a':
            if 'href' in attrs:
                self.attr['a'] = {'link': quote(attrs['href'])}
                if 'style' in attrs:
                    self.attr['a'].update(decode_style(attrs['style']))
        elif tag == 'img':
            self.txt += _parse_img(self.attr, attrs)
        elif tag == 'font':
            font = attrs.get('face', "")
            self.attr['code'] = font in _MONOSPACE_FONTS or decode_style(
                attrs.get('style', ""), _parse_font_style)
            if font and font not in _MONOSPACE_FONTS:
                logging.warning(f"Unknown font: {font}")
        elif tag == 'u':
            self.attr['underline'] = True
        elif tag in ('i', 'em'):
            self.attr['italic'] = True
        elif tag in ('b', 'strong'):
            self.attr['bold'] = True
        elif tag == 'sup':
            self.attr['sup'] = True
        elif tag in ('ul', 'br'):
            self.txt += '\n'
        elif tag == 'li':
            self.txt += "- "
        elif tag == 'h1':
            self.txt += "# "
        elif tag == 'body':
            self.running = True
        else:
            logging.warning(f"Unknown tag: {tag}")

    def handle_endtag(self, tag):
        """Handle state update when closing a tag."""
        if self.tags[-1] != tag:
            if self.tags[-1] == 'img':  # occasional error
                self.tags.pop()
            else:
                raise ValueError(
                    f"Unmatched tags, {tag} closing {self.tags[-1]}")

        if tag == 'ul':
            self.txt += "\n"
        elif tag == 'div':
            if len(self.tags) == self.div_lvl:
                self.div_lvl = 0
                self.txt += "```"
                logging.debug("codeblock")
            self.txt += "\n" if self.last != 'br' else ""
        elif tag == 'h1':
            self.txt += "\n\n"
        elif tag == 'span':
            if 'span' in self.attr and self.attr['span']:
                self.attr['span'].pop()
            else:
                self.attr.pop('span', False)
        elif tag == 'a':
            self.attr.pop('a', False)
        elif tag == 'font':
            self.attr.pop('code', False)
        elif tag == 'u':
            self.attr.pop('underline')
        elif tag in ('i', 'em'):
            self.attr.pop('italic')
        elif tag in ('b', 'strong'):
            self.attr.pop('bold')
        elif tag == 'sup':
            self.attr.pop('sup')
        elif tag == 'body':
            self.running = False

        self.last = self.tags.pop()

    def handle_data(self, data):
        """Handle tag content."""
        if not self.running:
            return
        if not data.strip():  # only spaces
            if self.tags[-1] in ('span', 'div'):
                self.txt += data  # formatting spaces may be in other tags
            return

        if self.attr.get('a', {}).get('color', False):
            # colored links are internal
            self.internal_links.append((data, unquote(self.attr['a']['link'])))
        self.txt += _format(data, self.attr)

    def finalize(self, hints, cr_="<br>"):
        """Clean up residual formatting."""
        # fix special characters
        txt = self.txt.replace(chr(160), "&nbsp;")
        txt = txt.replace(codecs.BOM_UTF8.decode('utf-8'), "")

        # remove multiple empty lines
        txt = re.sub(r"\n\n\n+", "\n\n", txt)
        # remove lone nbsp - needed before single cr conversion
        txt = re.sub(r"\n\**(&nbsp;)+\**\n", "\n\n", txt)
        # remove trailing spaces
        txt = re.sub(r" *\n", "\n", txt)
        txt = _replace_cr_except_code(txt, hints.get('codeblocks', []), cr_)

        return txt.strip() + '\n'


def convert_html_file(html, hints, cr_=r"\\"):
    """Convert HTML to Markdown."""
    parser = HTMLMDParser()
    parser.feed(html)
    parser.close()
    txt = parser.finalize(hints, cr_)

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
        converted.append(fname)

        with open(os.path.join(path, fname), 'r', encoding="utf-8") as fid:
            txt = fid.read()
        fname, _ = os.path.splitext(fname)
        txt, curr_links = convert_html_file(txt, hints['files'].get(fname, {}))

        out_file = os.path.join(path, f"{fname}.md")
        with open(out_file, 'w', encoding="utf-8") as fid:
            fid.write(txt)

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
