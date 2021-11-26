"""Converts Evernote HTML files to Markdown."""
import argparse
import logging
import json
import os
import re
from itertools import chain
from html.parser import HTMLParser


def parse_span_style(tokens):
    """Parse the style of span elements."""
    style = {}
    style['italic'] = tokens.get('font-style', None) == 'italic'
    style['bold'] = tokens.get('font-weight', None) == 'bold'
    style['underline'] = tokens.get('text-decoration', None) == 'underline'
    style['sub'] = tokens.get('vertical-align', None) == 'sub'
    style['code'] = "courier" in tokens.get('font-family', "").lower()

    return style


def parse_div_style(tokens):
    """Parse the style of div elements."""
    style = {}
    style['codeblock'] = tokens.get("-en-codeblock", "") == "true"
    return style


def decode_style(prop, parser=parse_span_style):
    """Decode style-specific attributes."""
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

    if attr.get('code', False):
        # spaces are non-breaking in code; remove leading/trailing spaces
        string = string.replace(u"\xc2\xa0", " ").strip()
        if not string:  # nothing left
            if warn:
                logging.warning("Empty code detected")
                return "<empty code>"
            return ""
        string = f"`{string}`"
    if attr.get('sup', False):
        string = f"<sup>{string}</sup>"
    if attr.get('sub', False):
        string = f"<sub>{string}</sub>"
    if attr.get('underline', False):
        string = f"<u>{string}</u>"
    if attr.get('italic', False):
        string = f"*{string}*"
    if attr.get('bold', False):
        string = f"**{string}**"
    if attr.get('link', None):
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


# check: https://docs.python.org/3/library/html.parser.html
class HTMLMDParser(HTMLParser):
    """Convert each HTML tag in a corresponding Markdown construct."""

    def __init__(self):
        """Initialize parser for a single document."""
        super(HTMLMDParser, self).__init__()
        self.tags, self.last, self.div_lvl = [], None, 0
        self.attr, self.running, self.txt = {}, False, ""

    def handle_starttag(self, tag, attrs):
        """Handle state update for a new tag."""
        logging.debug("Start tag: %s", tag)
        self.tags.append(tag)
        attrs = dict(attrs)

        if tag == 'span':
            if self.last == 'span':
                # hack: avoid bad syntax with two close spans
                self.txt += " "
            if 'style' in attrs:
                self.attr = decode_style(attrs['style'])
        elif tag == 'div' and 'style' in attrs:
            attr = decode_style(attrs['style'], parse_div_style)
            if attr.get('codeblock', False):
                self.div_lvl = len(self.tags)
                self.txt += "\n```bash\n"
        elif tag == 'a' and 'href' in attrs:
            if 'style' in attrs:
                self.attr = decode_style(attrs['style'])
            self.attr['link'] = attrs['href'].replace(" ", "_")
        elif tag == 'u':
            self.attr['underline'] = True
        elif tag == 'i':
            self.attr['italic'] = True
        elif tag == 'sup':
            self.attr['sup'] = True
        elif tag == 'ul':
            self.txt += '\n'
        elif tag == 'li':
            self.txt += "- "
        elif tag == 'h1':
            self.txt += "# "
        elif tag == 'body':
            self.running = True

    def handle_endtag(self, tag):
        """Handle state update when closing a tag."""
        if self.tags[-1] != tag:
            raise ValueError(f"Unmatched tags, {tag} closing {self.tags[-1]}")

        if tag == 'ul':
            self.txt += "\n"
        elif tag == 'div':
            if len(self.tags) == self.div_lvl:
                self.div_lvl = 0
                self.txt += "```"
            self.txt += "\n"
        elif tag == 'h1':
            self.txt += "\n\n"
        elif tag in ('span', 'a'):
            self.attr = {}
        elif tag == 'u':
            self.attr.pop('underline')
        elif tag == 'i':
            self.attr.pop('italic')
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
            return

        self.txt += _format(data, self.attr)

    def finalize(self, hints, cr_="<br>"):
        """Clean up residual formatting."""
        txt = self.txt.replace(u"\xc2\xa0", "&nbsp;")
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        # remove lone nbsp - neded before single cr conversion
        txt = re.sub(r"\n\**(&nbsp;)+\**\n", "\n\n", txt)
        txt = _replace_cr_except_code(txt, hints.get('codeblocks', []), cr_)

        return txt.strip() + '\n'


def main():
    """Script entry point."""
    parser = argparse.ArgumentParser(
        description='Convert Evernote HTML to Markdown')

    parser.add_argument("path", type=str, help="file to convert")
    parser.add_argument("-p", "--print", action="store_true",
                        help="print to stdout instead of writing files.")
    args = parser.parse_args()

    with open("hints.json", 'r') as fid:
        hints = json.load(fid)

    with open(args.path, 'r') as fid:
        txt = '\n'.join(line for line in fid)
    fname, _ = os.path.splitext(args.path)
    name = os.path.basename(fname)

    parser = HTMLMDParser()
    parser.feed(txt)
    parser.close()

    txt = parser.finalize(cr_=r"\\", hints=hints['files'].get(name, {}))

    if args.print:
        print(txt)
    else:
        with open(f"{fname}.md", 'w') as fid:
            fid.write(txt)

    # TODO: embed PDFs, videos, images
    # <object data="x.pdf" width="1000" height="1000" type='application/pdf'/>
    # <video muted autoplay controls>
    # <source src="{{ site.my-media-path }}/myvideo.mp4" type="video/mp4">
    # </video>


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
