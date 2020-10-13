"""Converts Evernote HTML files to Markdown."""
import argparse
import re
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

    return style


def _format(string, style):
    """Add formats to string."""
    if not style:
        return string

    if style.get('underline', False):
        string = f"<u>{string}</u>"
    if style.get('italic', False):
        string = f"*{string}*"
    if style.get('bold', False):
        string = f"**{string}**"

    return string


class HTMLMDParser(HTMLParser):
    # check: https://docs.python.org/3/library/html.parser.html

    def __init__(self):
        super(HTMLMDParser, self).__init__()
        self.tags, self.last = [], None
        self.style, self.link = None, None
        self.running, self.txt = False, ""

    def handle_starttag(self, tag, attrs):
        # print("Start tag:", tag)
        self.tags.append(tag)
        attrs = dict(attrs)
        if tag == 'span':
            if self.last == 'span':
                # hack: avoid bad syntax with two close spans
                self.txt += " "
            if 'style' in attrs:
                self.style = decode_style(attrs['style'])
        elif tag == 'a' and 'href' in attrs:
            self.link = attrs['href'].replace(" ", "_")
        elif tag == 'ul':
            self.txt += '\n'
        elif tag == 'li':
            self.txt += "- "
        elif tag == 'h1':
            self.txt += "# "
        elif tag == 'body':
            self.running = True

    def handle_endtag(self, tag):
        if self.tags[-1] != tag:
            raise ValueError(f"Unmatched tags, {tag} closing {self.tags[-1]}")

        if tag in ('div', 'ul'):
            self.txt += "\n"
        elif tag == 'span':
            self.style = None  # end of style block
        elif tag == 'a':
            self.link = None
        elif tag == 'body':
            self.running = False

        self.last = self.tags.pop()

    def handle_data(self, data):
        if not self.running:
            return

        if self.link:
            data = f"[{data}]({self.link})"
        data = _format(data, self.style)
        self.txt += data


def _finalize(txt, cr_="<br>"):
    """Clean up residual formatting."""
    txt = txt.replace(u"\xc2\xa0", "&nbsp;")
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    # explicit single returns except for bullet lists
    txt = re.sub(r"(\S)\n([^[\n-])", rf"\1{cr_}\n\2", txt)

    return txt.strip() + '\n'


def main():
    """Script entry point."""
    parser = argparse.ArgumentParser(
        description='Convert Evernote HTML to Markdown')

    parser.add_argument('path', type=str, help="file to convert")
    args = parser.parse_args()

    with open(args.path, 'r') as fid:
        txt = '\n'.join(line for line in fid)

    parser = HTMLMDParser()
    parser.feed(txt)

    txt = _finalize(parser.txt)
    with open("test.md", 'w') as fid:
        fid.write(txt)

    print(txt)


if __name__ == '__main__':
    main()
