"""Converts Evernote HTML files to Markdown using Beautiful Soup."""
import argparse
import codecs
import datetime
import logging
import json
import os
import re
from string import whitespace

from itertools import chain
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString


_MONOSPACE_FONTS = ["andale mono", "consolas", "courier new", "lucida console",
                    "monospace"]
_KNOWN_FONTS = _MONOSPACE_FONTS + [
    "arial", "calibri", "tahoma", "times new roman", "lucida sans unicode",
    "helvetica", "verdana", "windings"]
_WHITESPACE = tuple(whitespace) + (chr(160),)

# options
COLOR_INTERNAL_LINKS = True
INLINE_PREVIEWS = 'link'        # 'no', 'link', 'image'
IMAGE_MODE = 'html'


def parse_span_style(tokens):
    """Parse the style of span elements."""
    style = {}
    style['italic'] = tokens.get('font-style', None) == 'italic'
    style['bold'] = tokens.get('font-weight', None) == 'bold'
    style['underline'] = tokens.get('text-decoration', None) == 'underline'
    style['strikethrough'] = \
        tokens.get('text-decoration', None) == 'line-through'
    style['sub'] = tokens.get('vertical-align', None) == 'sub'
    style['sup'] = tokens.get('vertical-align', None) == 'super'
    style['code'] = "courier" in tokens.get('font-family', "").lower()
    style['color'] = tokens.get('color', False)

    return {k: v for k, v in style.items() if v}  # drop useless attributes


def _parse_div_style(tokens):
    style = {}
    style['codeblock'] = tokens.get("-en-codeblock", "") == "true"
    align = tokens.get('text-align', '').lower()
    style['align'] = align if align in ('center', 'right') else ''

    return {k: v for k, v in style.items() if v}  # drop useless attributes


def _parse_font_style(tokens):
    return {'code': "courier" in tokens.get('font-family', "").lower()}


def _parse_img(tag):
    """Parse img tags."""
    name = tag.get('data-filename', "")[:-4] or tag.get('alt', "")
    name = quote(os.path.splitext(name)[0])
    attr = {'src': tag.get('src'), 'alt': name,
            'width': tag.get('width'), 'height': tag.get('height')}

    if IMAGE_MODE == 'html':        # Joplin does not support MD images
        attr = ' '.join([f"{k}=\"{v}\"" for k, v in attr.items() if v])
        return f"<img {attr}>"
    elif IMAGE_MODE == 'markdown':  # for eg Obisidian
        size = f"|{attr['width']}" if attr['width'] else ""
        size += f"x{attr['height']}" if attr['height'] else ""
        return f"![{attr['alt']}{size}]({attr['src']})"

    raise ValueError("Unknown image mode: {IMAGE_MODE}")


def _escape(string):
    """Escape reserved Markdown characters."""
    string = string.replace("<", r"\<").replace(">", r"\>").replace("$", r"\$")
    return string.replace("*", r"\*").replace("`", r"\`")

def _unescape(string):
    """Unescape reserved Markdown characters for codeblocks."""
    string = string.replace(r"\>", ">").replace(r"\<", "<").replace(r"\$", "$")
    return string.replace(r"\*", "*").replace(r"\`", "`")


def decode_style(prop, parser=parse_span_style):
    """Decode style-specific attributes."""
    if not prop:
        return {}

    # occasionally, Evernote drops the font-family attribute
    prop = re.sub("\"(;} |;)\"", "\"Courier New\"", prop)
    tokens = [t.strip() for t in prop.split(';') if t]
    tokens = dict([tuple(x.strip() for x in t.split(':', maxsplit=1))
                   for t in tokens])
    # handle aliases
    tokens['text-decoration'] = tokens.pop('text-decoration-line',
                                           tokens.get('text-decoration', None))

    return parser(tokens)


def convert_tr(el, text):
    """Convert table rows; from Markdownify."""
    cells = el.find_all(['td', 'th'])
    is_headrow = all([cell.name == 'th' for cell in cells])
    overline = ''
    underline = ''
    if is_headrow and not el.previous_sibling:
        # first row and is headline: print headline underline
        underline += '| ' + ' | '.join(['---'] * len(cells)) + ' |' + '\n'
    elif (not el.previous_sibling
            and (el.parent.name == 'table'
                or (el.parent.name == 'tbody'
                    and not el.parent.previous_sibling))):
        # first row, not headline, and:
        # - the parent is table or
        # - the parent is tbody at the beginning of a table.
        # print empty headline above this row
        overline += '| ' + ' | '.join([''] * len(cells)) + ' |' + '\n'
        overline += '| ' + ' | '.join(['---'] * len(cells)) + ' |' + '\n'
    return overline + '|' + text + '\n' + underline


def _clean_code_block(string):
    """Clean code blocks."""
    # spaces are non-breaking in code; remove leading/trailing spaces
    string = string.replace("&nbsp;", " ")
    string = _unescape(string.replace(chr(160), " ").strip())

    if not string:  # nothing left
        logging.warning("Empty code detected")
        
    return string


def _format(string, attr, warn=True):
    """Add formats to string; beware of the order."""
    if not attr or string.isspace():
        return string

    if string.endswith(_WHITESPACE) or string.startswith(_WHITESPACE):
        # remove formatting from leading/trailing whitespace to avoid MD errors
        clean = string.strip(''.join(_WHITESPACE))
        off, size = string.find(clean[0]), len(clean)
        return string[:off] + _format(clean, attr, warn) + string[off+size:]
    
    if attr.get('codeblock', False):
        lang = attr.get('lang', 'bash')
        return f"\n```{lang}\n{_clean_code_block(string)}\n```"

    if attr.get('code', ''):
        # spaces are non-breaking in code; remove leading/trailing spaces
        string = _clean_code_block(string)
        if not string:
            return ""

        string = f"<code>{string}</code>" if attr['code'] == 'html' \
            else f"`{string}`"

    if attr.get('color', "") == "rgb(0, 0, 0)":
        attr.pop('color')  # remove default color
    if string and string[0] == '#' and re.match(r'#+ ', string):
        # move headings out of formatting
        head, string = string.split(' ', maxsplit=1)
        head = head + ' '
    else:
        head = ""

    # links in italics are normally definitions and use underline by default
    #  but log the instances we found
    if attr.get('link', None) and attr.get('italic', False):
        if not attr.get('underline', False):
            attr['underline'] = True
            logging.debug("Link in italics without underline: %s", string)

    if attr.get('abbr', None):
        string = f"<abbr title=\"{attr['abbr']}\">{string}</abbr>"
    if attr.get('sup', False):
        string = f"<sup>{string}</sup>"
    if attr.get('sub', False):
        string = f"<sub>{string}</sub>"
    if attr.get('underline', False):
        string = f"<u>{string}</u>"
    if attr.get('strikethrough', False):
        string = f"~~{string}~~"
    if attr.get('color', False):
        string = f"<span style=\"color: {attr['color']}\">{string}</span>"
    if attr.get('italic', False):
        string = f"*{string}*"
    if attr.get('bold', False):
        string = f"**{string}**"
    if attr.get('align', ''):
        string = string.replace('\n', '<br>')  # only BR works with divs
        string = f"<div align=\"{attr['align']}\">{string}</div>"
    if attr.get('link', None):
        string = f"[{string}]({attr['link']})"
    if attr.get('heading', 0):
        string = f"{'#' * attr['heading']} {string}"

    return head + string


def _format_spans(spans):
    """Format a list of spans."""
    if len(spans) == 1:
        return _format(*spans[0])

    texts, styles = zip(*spans)
    if all(s == styles[0] for s in styles):
        return _format(''.join(texts), styles[0])

    # factor out common styles to avoid markdown gotchas
    styles = [set(s.items()) for s in styles]
    base_style = styles[0].intersection(*styles[1:])
    styles = [dict(s.difference(base_style)) for s in styles]

    base_style = dict(base_style)
    if 'code' in base_style:
        base_style['code'] = 'html'  # to handle formatting in code

    chunk = ''.join(_format(t, s) for t, s in zip(texts, styles))
    return _format(chunk, base_style)  # also apply base style


def _replace_cr_except_code(txt, langs, cr_):
    """Replace single newlines except for bullet lists and code blocks."""
    codeblocks = list(re.finditer(r"```bash([\S|\s]+?)```", txt))
    if langs and len(codeblocks) != len(langs):
        raise ValueError("Wrong number of code block hints.")

    steps = [0] + list(chain(*[c.span() for c in codeblocks])) + [len(txt)]
    segs = [txt[s:e] for s, e in zip(steps[:-1], steps[1:])]

    for idx, seg in enumerate(segs):
        if idx % 2 == 0:  # normal text
            segs[idx] = re.sub(r"(\S)\n([^\n-])", rf"\1{cr_}\n\2", seg)
        elif langs:       # code block - keep \n and change language if needed
            lang = langs[idx // 2]
            if lang != "bash":
                segs[idx] = re.sub("```bash", f"```{lang}", seg)

    return ''.join(segs)


def print_metadata(meta):
    """Print metadata as Joplin frontmatter."""
    def _reformat_date(date):
        """Reformat date."""
        # add 'Z' because zero UTC offset not supported in Python
        return datetime.datetime.strptime(date, '%m/%d/%Y %I:%M %p').strftime(
            '%Y-%m-%d %H:%M:%S') + 'Z'

    if 'created' in meta:
        meta['created'] = _reformat_date(meta['created'])
    if 'updated' in meta:
        meta['updated'] = _reformat_date(meta['updated'])
    if 'tags' in meta:
        meta['tags'] = '\n' + '\n'.join([f"  - {t}" for t in meta['tags']])

    keys_ordered = ('title', 'updated', 'created', 'source', 'author', 'tags')
    txt = '\n'.join([f"{k}: {meta[k]}" for k in keys_ordered if k in meta])

    return f"---\n{txt}\n---\n\n"


def finalize(txt, hints, cr_="<br>"):
    """Clean up residual formatting."""
    # fix special characters
    txt = txt.replace(chr(160), "&nbsp;")
    txt = txt.replace(codecs.BOM_UTF8.decode('utf-8'), "")

    # remove multiple empty lines
    txt = re.sub(r"\n\n\n+", "\n\n", txt)
    # remove lone nbsp - needed before single cr conversion
    txt = re.sub(r"\n\**(&nbsp;)+\**\n", "\n\n", txt)
    # nbsp not at line start are spaces
    txt = re.sub(r"&nbsp;(?![&nbsp;| |*]+)", " ", txt)
    # remove trailing spaces
    txt = re.sub(r" +\n", "\n", txt)
    txt = _replace_cr_except_code(txt, hints.get('codeblocks', []), cr_)

    return txt.strip() + '\n'


class HTMLParser():
    """Convert HTML to Markdown."""

    def __init__(self, html, cr_=r"\\", hints=None):
        """Initialize parser for a single document."""
        self.cr_ = cr_
        self.hints = hints or {}
        self.internal_links = []
        with open(html, 'r', encoding='utf-8-sig') as fid:
            self.soup = BeautifulSoup(fid, 'html.parser')
    
    def parse_metadata(self, as_frontmatter=False):
        """Extract metadata from note."""
        meta = {}
        tag = self.soup.find('h1')
        if tag:
            meta['title'] = tag.text
            tag.extract()

        table = self.soup.find('table')  # metadata table
        if table:
            tds = table.find_all('td')
            for key, val in zip(tds[::2], tds[1::2]):  # iterate over pairs
                key = key.text[:-1].strip().lower()
                val = val.text.strip()
                if key == 'tags':
                    val = val.split(', ')

                meta[key] = val
            table.extract()
        
        if as_frontmatter:
            return print_metadata(meta)

        return meta

    def parse(self):
        """Convert HTML to Markdown."""
        tag = next(self.soup.children)  # gwt HTML tag
        tag = next((el for el in tag.children if el.name == 'body'), None)
        if tag is None:
            raise ValueError("No content found")

        txt, _ = self.process_tag(tag)
        return finalize(txt, self.hints, self.cr_)

    def _parse_link(self, tag, txt, style):
        """Format a link."""
        link, name = tag.get('href', ''), tag.get('name', '')
        if name:  # anchor, drop it
            return tag.text, {}

        if not link or link == "https:":  # anchor or empty link
            logging.warning(f"Empty link found for {txt}")
            return tag.text, {}
        if link == tag.text:
            return tag.text, {}  # use autolink

        style = decode_style(tag.get('style', ''))
        inter = style.get('color', False) in ("rgb(105, 170, 53)", "#69aa35")

        if '://' not in link:  # internal link
            if link.endswith('.html'):
                self.internal_links.append((tag.text, link))
                link = link.replace(".html", ".md")
            elif link.endswith(('.mp4', '.pdf')) and INLINE_PREVIEWS:
                # discard preview image and link file directly
                txt = os.path.basename(link)

            if not COLOR_INTERNAL_LINKS and inter:
                # remove color for internal links
                style.pop('color')
            link = quote(link)
        elif inter:
            logging.warning(f"Unresolved internal link: {txt} {link}")

        style['link'] = link
        return txt, style
    
    def _merge_tags(self, children):
        """Merge text from children."""
        txt, style, chunks, spans = "", {}, [], []
        for tag in children:
            old_txt, old_style = txt, style
            txt, style = self.process_tag(tag)

            if tag.name == 'span':
                # delay adding spans to chunks and check for consecutive spans
                if spans and old_style == style:
                    spans[-1][0] += txt   # merge with previous span
                else:
                    spans.append([txt, style])
                if not tag.next_sibling or tag.next_sibling.name != 'span':
                    chunks.append(_format_spans(spans))
                    spans = []
                continue
            if tag.name == 'div' and not old_txt.endswith('\n'):
                txt = '\n' + txt
            chunks.append(_format(txt, style))

        return ''.join(chunks)

    def process_tag(self, tag):
        """Process a tag."""
        if isinstance(tag, NavigableString):
            return _escape(str(tag)), {}

        style, txt, children = {}, tag.text, list(tag.children)
        if len(children) > 1:
            txt = self._merge_tags(children)
        elif len(children) == 1:
            txt, style = self.process_tag(children[0])
            # if tag.name != 'span':  # stop here
            #     txt, style = _format(txt, style), {}

        if tag.name == 'div':
            div_style = decode_style(tag.get('style', ''), _parse_div_style)
            if div_style.get('codeblock', False):
                style['codeblock'], style['lang'] = True, 'bash'
        elif tag.name == 'pre':  # pre-formatted text
            style['codeblock'], style['lang'] = True, ''
        elif tag.name == 'span':
            if len(children) <= 1:
                # if multiple children, their style overrides the parent
                style.update(decode_style(tag.get('style', '')))
        elif tag.name == 'hr':  # horizontal rule
            txt = f"\n----\n"
        elif tag.name.startswith('h'):  # heading
            style = decode_style(tag.get('style', ''), _parse_div_style)
            style['heading'] = int(tag.name[1])
        elif tag.name == 'a':
            txt, style = self._parse_link(tag, txt, style)
        elif tag.name == 'img':
            txt = _parse_img(tag)
        elif tag.name == 'font':
            font = tag.get('face', "").split(',')[0].strip("'").lower()
            style['code'] = font in _MONOSPACE_FONTS or decode_style(
                tag.get('style', ""), _parse_font_style).get('code', False)
            if font and font not in _KNOWN_FONTS:
                logging.warning(f"Unknown font: {font}")
        elif tag.name == 'blockquote':
            txt = '> ' + txt.replace('\n', '\n> ')
        elif tag.name == 'p':
            txt = f"{txt}\n"
        elif tag.name == 'br':
            txt = '\n'
        elif tag.name in ('ul', 'ol'):
            txt = f"\n{txt}"
        elif tag.name == 'li':
            mark = '- ' if tag.parent.name == 'ul' else '1. '
            txt = f"{mark}{txt}\n"
        elif tag.name == 'u':
            style['underline'] = True
        elif tag.name in ('del', 'strike', 's'):
            style['strikethrough'] = True
        elif tag.name in ('code', 'tt'):
            style['code'] = True
        elif tag.name in ('i', 'em'):
            style['italic'] = True
        elif tag.name in ('b', 'strong'):
            style['bold'] = True
        elif tag.name == 'q':
            txt = f'"{txt}"'
        elif tag.name == 'sup':
            style['sup'] = True
        elif tag.name == 'sub':
            style['sub'] = True
        elif tag.name == 'small':
            style['sub'], style['sup'] = True, True
        elif tag.name == 'center':
            style['align'] = 'center'
        elif tag.name == 'abbr':
            style['abbr'] = tag.get('title', ' ')
        elif tag.name in ('table', 'tbody'):
            txt = f'\n\n{txt}\n'
        elif tag.name == 'tr':
            txt = convert_tr(tag, txt)
        elif tag.name in ('td', 'th'):
            txt = f' {txt} |'
        elif tag.name in ('body', 'cite', 'address'):
            pass   # ignore tags
        else:
            logging.warning(f"Unknown tag: {tag.name}")

        return txt, style


def convert_html_to_markdown(html, hints, parse_metadata=True):
    """Convert HTML to Markdown."""
    parser = HTMLParser(html, hints=hints)
    txt = parser.parse_metadata(as_frontmatter=True) \
        if parse_metadata else ''
    txt += parser.parse()

    return txt, parser.internal_links


def main():
    """Script entry point."""
    parser = argparse.ArgumentParser(
        description='Convert Evernote HTML to Markdown')

    parser.add_argument("path", type=str, help="file to convert")
    parser.add_argument("-m", "--mode", default="single",
                        help="recursively convert all files following links.")
    args = parser.parse_args()

    with open("hints.json", 'r') as fid:
        hints = json.load(fid)

    path = os.path.dirname(args.path)
    all_files = sorted(f for f in os.listdir(path) if f.endswith('.html'))
    all_files = all_files[::-1]  # to have ascending order with pop()
    links = [(path, f) for f in all_files] \
        if args.mode == 'all' else [os.path.split(args.path)]
    
    converted = []
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

        if args.mode == 'recursive':
            links += [(path, f) for _, f in curr_links if f not in converted]

    if args.mode == 'recursive':
        print("\nMissing files:")
        for fname in set(all_files) - set(converted):
            print(f"{fname}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
