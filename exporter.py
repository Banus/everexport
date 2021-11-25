"""Converts Evernote HTML files to Markdown."""
import argparse
import os
import re
import sys
from html.parser import HTMLParser


def decode_style(prop):
    """Decode style-specific attributes."""
    tokens = [t.strip() for t in prop.split(';') if t]
    tokens = dict([tuple(x.strip() for x in t.split(':')) for t in tokens])
    # handle aliases
    tokens['text-decoration'] = tokens.pop('text-decoration-line',
                                           tokens.get('text-decoration', None))

    style = {}
    style['italic'] = tokens.get('font-style', None) == 'italic'
    style['bold'] = tokens.get('font-weight', None) == 'bold'
    style['underline'] = tokens.get('text-decoration', None) == 'underline'
    style['sub'] = tokens.get('vertical-align', None) == 'sub'
    style['code'] = "courier" in tokens.get('font-family', "").lower()

    return style


def _format(string, attr, warn=True):
    """Add formats to string; beware of the order."""
    if not attr:
        return string

    if attr.get('code', False):
        # spaces are non-breaking in code; remove leading/trailing spaces
        string = string.replace(u"\xc2\xa0", " ").strip()
        if not string:  # nothing left
            if warn:
                print("Empty code detected", file=sys.stderr)
                return "<empty code>"
            return ""
        string = f"`{string}`"
    if attr.get('sup', False):
        string = f"<sup>{string}</sup>"
    if attr.get('sub', False):
        string = f"<sub>{string}</sub>"
    if attr.get('underline', False):
        string = f"<u>{string}</u>"
    if attr.get('link', None):
        string = f"[{string}]({attr['link']})"
    if attr.get('italic', False):
        string = f"*{string}*"
    if attr.get('bold', False):
        string = f"**{string}**"

    return string


# check: https://docs.python.org/3/library/html.parser.html
class HTMLMDParser(HTMLParser):
    """Convert each HTML tag in a corresponding Markdown construct."""

    def __init__(self):
        """Initialize parser for a single document."""
        super(HTMLMDParser, self).__init__()
        self.tags, self.last, self.span_keys = [], None, []
        self.attr, self.running, self.txt = {}, False, ""

    def handle_starttag(self, tag, attrs):
        """Handle state update for a new tag."""
        # print("Start tag:", tag)
        self.tags.append(tag)
        attrs = dict(attrs)
        if tag == 'span':
            if self.last == 'span':
                # hack: avoid bad syntax with two close spans
                self.txt += " "
            if 'style' in attrs:
                self.attr = decode_style(attrs['style'])
                self.span_keys = list(self.attr.keys())
        elif tag == 'a' and 'href' in attrs:
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

        if tag in ('div', 'ul'):
            self.txt += "\n"
        elif tag == 'h1':
            self.txt += "\n\n"
        elif tag == 'span':
            self.attr = {}
        elif tag == 'a':
            self.attr.pop('link', None)
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

    def finalize(self, cr_="<br>"):
        """Clean up residual formatting."""
        txt = self.txt.replace(u"\xc2\xa0", "&nbsp;")
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        # remove lone nbsp - neded before single cr conversion
        txt = re.sub(r"\n\**(&nbsp;)+\**\n", "\n\n", txt)
        # explicit single returns except for bullet lists
        txt = re.sub(r"(\S)\n([^\n-])", rf"\1{cr_}\n\2", txt)

        return txt.strip() + '\n'


def main():
    """Script entry point."""
    parser = argparse.ArgumentParser(
        description='Convert Evernote HTML to Markdown')

    parser.add_argument("path", type=str, help="file to convert")
    parser.add_argument("-p", "--print", action="store_true",
                        help="print to stdout instead of writing files.")
    args = parser.parse_args()

    with open(args.path, 'r') as fid:
        txt = '\n'.join(line for line in fid)
    fname, _ = os.path.splitext(args.path)

    parser = HTMLMDParser()
    parser.feed(txt)
    parser.close()

    txt = parser.finalize(cr_=r"\\")

    if args.print:
        print(txt)
    else:
        with open(f"{fname}.md", 'w') as fid:
            fid.write(txt)

    # TODO: fix code block
    # TODO: embed PDFs, videos, images
    # <object data="x.pdf" width="1000" height="1000" type='application/pdf'/>
    # <video muted autoplay controls>
    # <source src="{{ site.my-media-path }}/myvideo.mp4" type="video/mp4">
    # </video>


if __name__ == '__main__':
    main()
