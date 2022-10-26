"""Converts Evernote HTML files to Markdown using Beautiful Soup."""
import argparse
import codecs
import datetime
import json
import logging
import os
import re
import shutil
import time
from string import whitespace

from itertools import chain
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup, NavigableString


_MONOSPACE_FONTS = ["andale mono", "consolas", "courier new", "lucida console",
                    "monospace"]
_KNOWN_FONTS = _MONOSPACE_FONTS + [
    "arial", "calibri", "tahoma", "times new roman", "lucida sans unicode",
    "helvetica", "verdana", "wingdings"]
_WHITESPACE = tuple(whitespace) + (chr(160),)

_ESCAPE_CHARS = "<>$*`-_"
_ESCAPE_DICT = {c : rf'\{c}' for c in _ESCAPE_CHARS}

# options
COLOR_INTERNAL_LINKS = False
USE_WIKILINKS = True            # use [[link|name]] instead of [name](link)
MD_DEFINITIONS = False          # Markdown-style definition lists
INLINE_PREVIEWS = 'link'        # 'no', 'link', 'image'
IMAGE_MODE = 'markdown'         # 'html', 'markdown'


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

def _escape(string):
    """Escape reserved Markdown characters."""
    for char, esc in _ESCAPE_DICT.items():
        string = string.replace(char, esc)
    return string

def _unescape(string):
    """Unescape reserved Markdown characters for codeblocks."""
    for char, esc in _ESCAPE_DICT.items():
        string = string.replace(esc, char)
    return string


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
    if not attr or not string or not string.strip(''.join(_WHITESPACE)):
        return string

    if string.endswith(_WHITESPACE) or string.startswith(_WHITESPACE):
        # remove formatting from leading/trailing whitespace to avoid MD errors
        clean = string.strip(''.join(_WHITESPACE))
        off, size = string.find(clean[0]), len(clean)
        return string[:off] + _format(clean, attr, warn) + string[off+size:]
    
    if attr.get('codeblock', False):
        lang = attr.get('lang', 'bash')
        return f"\n\n```{lang}\n{_clean_code_block(string)}\n```\n\n"

    if attr.get('code', ''):
        # spaces are non-breaking in code; remove leading/trailing spaces
        string = _clean_code_block(string)
        if not string:
            return ""
        
        if attr['code'] == 'html':
            string = f"<code>{string}</code>"
        elif '`' in string:
            # avoid rendering errors when the code contains backticks
            if string.startswith('`'):
                string = ' ' + string
            if string.endswith('`'):
                string += ' '
            string = f"``{string}``"
        else:
            string = f"`{string}`"

    if attr.get('color', "") in ("rgb(0, 0, 0)", "black"):
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
    if attr.get('align', '') and attr['align'] in ('center', 'right'):
        string = string.replace('\n', '<br>')  # only BR works with divs
        string = f"<div align=\"{attr['align']}\">{string}</div>"
    if attr.get('link', None):
        # wikilink only for internal non-formatted links
        if USE_WIKILINKS and attr['link'].endswith('.md') and \
                all(c not in string for c in '/<*~`'):
            lnk = unquote(attr['link'][:-3])
            string = f"[[{lnk}]]" if lnk == string else f"[[{lnk}|{string}]]"
        else:
            string = f"[{string}]({attr['link']})"
    if attr.get('heading', 0):
        string = f"\n\n{'#' * attr['heading']} {string}\n\n"

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


def _replace_cr_except_code(txt, cr_):
    """Replace single newlines except for bullet lists and code blocks."""
    codeblocks = list(re.finditer(r"```bash([\S|\s]+?)```", txt))

    steps = [0] + list(chain(*[c.span() for c in codeblocks])) + [len(txt)]
    segs = [txt[s:e] for s, e in zip(steps[:-1], steps[1:])]

    for idx, seg in enumerate(segs):
        if idx % 2 == 0:  # normal text
            # add single newlines but not for lists, tables and definitions
            segs[idx] = re.sub(r"(\S)\n(?!\n| *[\|\-|1|:])",
                               rf"\1{cr_}\n", seg)

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


def finalize(txt, cr_="<br>"):
    """Clean up residual formatting."""
    # fix special characters
    txt = txt.replace(chr(160), "&nbsp;").replace('\t', '    ')
    txt = txt.replace(codecs.BOM_UTF8.decode('utf-8'), "")

    # nbsp not at line start are spaces
    txt = re.sub(r"&nbsp;(?![&nbsp;| ]+)", " ", txt)
    # remove trailing spaces
    txt = re.sub(r" +\n", "\n", txt)
    # remove lone nbsp - needed before single cr conversion
    txt = re.sub(r"\n\**(&nbsp;)+\**\n", "\n\n", txt)
    # remove multiple empty lines
    txt = re.sub(r"\n\n\n+", "\n\n", txt)
    # space before first nbsp in line to create Markdown blocks
    txt = re.sub(r"\n(&nbsp;)", r"\n \1", txt)

    txt = _replace_cr_except_code(txt, cr_)

    return txt.strip() + '\n'


class HTMLParser():
    """Convert HTML to Markdown."""

    def __init__(self, html, cr_=r"\\"):
        """Initialize parser for a single document."""
        self.cr_ = cr_
        self.path = ''
        self.internal_links, self.resources = [], []
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

        # notebook path as a special tag starting with 'nb:'
        if meta.get('tags', None):
            for tag in meta['tags']:
                if tag.startswith('nb:'):
                    self.path = os.path.normpath(tag[3:])
                    meta['tags'].remove(tag)
                    break
        
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
        return finalize(txt, self.cr_)

    def _parse_link(self, tag, txt, style):
        """Format a link."""
        link = tag.get('href', '')
        if not link:  # anchor, drop it
            return tag.text, {}

        if link == "https:":
            logging.warning(f"Empty link found for {txt}")
            return tag.text, {}
        if link == tag.text:
            return tag.text, style  # use autolink
        if link.startswith('#'):  # internal anchor
            return txt, style

        style = decode_style(tag.get('style', ''))
        inter = style.get('color', False) in ("rgb(105, 170, 53)", "#69aa35")

        if ':' not in link:  # internal link
            if link.endswith('.html'):
                self.internal_links.append(link)
                link = link.replace(".html", ".md")
            else:
                self.resources.append(link)
                if link.endswith(('.mp4', '.pdf')) and INLINE_PREVIEWS:
                    # discard preview image and link file directly
                    txt = os.path.basename(link)

            if not COLOR_INTERNAL_LINKS and inter:
                # remove color for internal links
                style.pop('color')
            link = quote(link.replace('\\', '/'))
        elif inter:
            logging.warning(f"Unresolved internal link: {txt} {link}")

        style['link'] = link
        return txt, style
    
    def _parse_img(self, tag):
        """Parse img tags."""
        src = tag.get('src', '')
        if '://' in src:
            logging.warning(f"External image found: {tag['src']}")
        else:
            self.resources.append(src)
            src = quote(src.replace('\\', '/'))

        name = tag.get('data-filename', "")[:-4] or tag.get('alt', "")
        name = os.path.basename(src) if not name else name
        attr = {'src': src, 'alt': os.path.splitext(name)[0],
                'width': tag.get('width'), 'height': tag.get('height')}

        if IMAGE_MODE == 'html':        # Joplin does not support MD images
            attr = ' '.join([f"{k}=\"{v}\"" for k, v in attr.items() if v])
            return f"<img {attr}>"
        elif IMAGE_MODE == 'markdown':  # for eg Obisidian
            size = f"|{attr['width']}" if attr['width'] else ""
            size += f"x{attr['height']}" if attr['height'] else ""
            return f"![{attr['alt']}{size}]({attr['src']})"

        raise ValueError("Unknown image mode: {IMAGE_MODE}")
    
    def _parse_definition(self, tag, use_md=MD_DEFINITIONS):
        """Format a definition."""
        if use_md:
            txt = ''
            for i, child in tag.children:
                if i == 0 and child.name != 'dt':
                    # first definition tag, not suported in Markdown
                    logging.warning(f"Invalid <dt> use.")
                    return self._parse_definition(tag, use_md=False)
                text = self._merge_tags(child.children)
                txt += f"\n{text}\n" if child.name == 'dt' else f": {text}\n"
            
            return '\n' + txt
        
        tags = [f"<{t.name}>{self._merge_tags(t.children)}</{t.name}>"
                for t in tag.children]
        return f"<{tag.name}>{''.join(tags)}</{tag.name}>\n\n"
    
    def _parse_table(self, tag):
        """Format a table."""
        def _parse_cell(tag):
            """Format a table cell."""
            return self._merge_tags(tag.children).strip()

        # first get all valid cells
        cells = []
        for tr in tag.children:  # if nested tags, skip colgroups
            if isinstance(tr, NavigableString):
                continue
            if tr.name in ('thead', 'tbody', 'tfoot'):
                cells += [[c for c in r.children if c.name in ('td', 'th')]
                          for r in tr.children if r.name == 'tr']
            elif tr.name == 'tr':
                cells.append([c for c in tr.children if c.name in ('td', 'th')])
        
        if not cells:
            return ''
        if len(cells) ==1 and len(cells[0]) == 1:
            # single cell, skip table
            return '\n' + _parse_cell(cells[0][0]) + '\n\n'

        n_cols = sum(int(c.get('colspan', 1)) for c in cells[0])
        table = [[None for _ in range(n_cols)] for _ in range(len(cells))]

        for i, row in enumerate(cells):
            col = 0
            for cell in row:
                while table[i][col] is not None:  # first non-empty cell
                    col += 1
                table[i][col] = _parse_cell(cell).replace('\n', '<br>')

                if cell.get('colspan', 1) != 1:
                    for j in range(1, int(cell['colspan'])):
                        table[i][col + j] = ''
                if cell.get('rowspan', 1) != 1:
                    for j in range(1, int(cell['rowspan'])):
                        table[i + j][col] = '^'

        row_txts = ['|' + '|'.join(row) + '|' for row in table]
        # an header separator is required for valid Markdown
        row_txts.insert(1, '|' + '|'.join(['---' for _ in table[0]]) + '|')

        return '\n' + '\n'.join(row_txts) + '\n\n'
    
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
            if not tag.name and 'code' not in style:
                txt = txt.replace('\n', ' ')  # no newlines in HTML text
                if tag.parent.name in ('ul', 'ol'):
                    txt = txt.strip()   # avoid spaces messing up lists
            chunks.append(_format(txt, style))

        return ''.join(chunks)

    def process_tag(self, tag):
        """Process a tag."""
        if isinstance(tag, NavigableString):
            return _escape(str(tag)), {}

        style, txt, children = {}, tag.text, list(tag.children)
        if tag.name not in ('table', 'dl'):  # skip custom tree tags
            if len(children) > 1:
                txt = self._merge_tags(children)
            elif len(children) == 1:
                txt, style = self.process_tag(children[0])

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
            txt = f"\n----\n\n"
        elif tag.name.startswith('h'):  # heading
            style = decode_style(tag.get('style', ''), _parse_div_style)
            style['heading'] = int(tag.name[1])
        elif tag.name == 'a':
            txt, style = self._parse_link(tag, txt, style)
        elif tag.name == 'img':
            txt =self._parse_img(tag)
        elif tag.name == 'font':
            font = tag.get('face', "").split(',')[0].strip("'").lower()
            style['code'] = font in _MONOSPACE_FONTS or decode_style(
                tag.get('style', ""), _parse_font_style).get('code', False)
            if font and font not in _KNOWN_FONTS:
                logging.warning(f"Unknown font: {font}")
        elif tag.name == 'blockquote':
            txt = '\n\n> ' + txt.strip() + '\n\n'
        elif tag.name == 'p':
            txt = f"{txt}\n"
            if tag.get('align', ''):
                style['align'] = tag['align']
        elif tag.name == 'br':
            txt = '\n'
        elif tag.name in ('ul', 'ol'):
            parent = tag.parent
            parent = parent.parent if parent.name == 'li' else parent
            if parent.name == 'ul':  # nested list, pad with space
                txt = '  ' + txt.replace('\n', '\n  ')
            elif parent.name == 'ol':
                txt = '   ' + txt.replace('\n', '\n   ')
            txt = f"\n\n{txt}\n\n"
        elif tag.name == 'li':
            if next(tag.children).name not in ('ul', 'ol'):
                mark = '- ' if tag.parent.name == 'ul' else '1. '
                txt = f"{mark}{txt.strip()}\n"
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
        elif tag.name == 'dl':
            txt = self._parse_definition(tag)
        elif tag.name in ('table', 'tbody'):
            txt = self._parse_table(tag)
        elif tag.name in ('body', 'cite', 'address'):
            pass   # ignore tags
        else:
            logging.warning(f"Unknown tag: {tag.name}")

        return txt, style


def convert_html_to_markdown(html, parse_metadata=True):
    """Convert HTML to Markdown."""
    parser = HTMLParser(html)
    txt = parser.parse_metadata(as_frontmatter=True) \
        if parse_metadata else ''
    txt += parser.parse()

    return txt, parser


def _convert_file(path, fname, out_dir='', is_test=False):
    """Convert a single file and copy its resource files."""
    logging.info(f"Converting {fname}")

    out_name, _ = os.path.splitext(fname)
    txt, parser = convert_html_to_markdown(
        os.path.join(path, fname))

    if not is_test:
        if out_dir:
            out_path = os.path.join(out_dir, parser.path)
            os.makedirs(out_path, exist_ok=True)
        else:
            out_path = path

        out_file = os.path.join(out_path, f"{out_name}.md")
        with open(out_file, 'w', encoding="utf-8") as fid:
            fid.write(txt)

        if out_dir and parser.resources:
            res_path = os.path.join(out_dir, 'resources')
            for res in parser.resources:
                out_path = os.path.join(res_path, res)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                shutil.copyfile(os.path.join(path, res), out_path)
    
    return parser.internal_links


def find_roots(converted):
    """Find root nodes in the converted tree."""
    try:
        import networkx as nx
    except ImportError:
        logging.warning("NetworkX not installed, skipping root detection")
        return []

    net = nx.DiGraph()
    for node, links in converted.items():
        net.add_node(node)
        for link in links:
            if link in converted:  # ignore missing links
                net.add_edge(node, link)

    return [(next(iter(island)),
             [node for node in island if not list(net.predecessors(node))])
            for island in nx.weakly_connected_components(net)
            if len(island) > 1]


def main():
    """Script entry point."""
    parser = argparse.ArgumentParser(
        description='Convert Evernote HTML to Markdown')

    parser.add_argument("path", type=str, help="file to convert")
    parser.add_argument("-m", "--mode", default="single",
                        help="recursively convert all files following links.")
    parser.add_argument("-o", "--output", default="", help="output directory"
                        "for converted files (default: in-place)")
    parser.add_argument("-v", "--verbose", type=int, default=0,
                        help="verbosity level (0-2)")
    parser.add_argument("-t", "--test", action="store_true",
                        help="test mode, don't write files")

    args = parser.parse_args()

    if args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose == 1:
        logging.basicConfig(level=logging.INFO)


    path = os.path.dirname(args.path)
    all_files = sorted(f for f in os.listdir(path) if f.endswith('.html'))
    all_files = all_files[::-1]  # to have ascending order with pop()
    links = [(path, f) for f in all_files] \
        if args.mode == 'all' else [os.path.split(args.path)]
    
    converted = {}
    start = time.time()
    while links:
        path, fname = links.pop()
        internal_links = _convert_file(path, fname, args.output, args.test)
        converted[fname] = internal_links

        if args.mode == 'recursive':
            links += [(path, f) for f in internal_links if f not in converted]

    if args.mode == 'recursive':
        print("\nMissing files:")
        for fname in set(all_files) - set(converted):
            print(f"{fname}")
    elif args.mode == 'all':
        print("\nMissing links:\n--------------")
        links = [set(l) for l in converted.values()]
        for fname in links[0].union(*links[1:]) - set(converted):
            print(f"{fname}")

        roots_per_file = find_roots(converted)
        print("\nRoot files:\n-----------")
        for first, roots in roots_per_file:
            if len(roots) > 1:
                print(f"{' '.join(roots)} (multiple roots)")
            elif not roots:
                print(f"{first} (no root)")
            else:
                print(f"{roots[0]}")
    
    elapsed = time.time() - start
    print(f"Converted {len(list(converted))} files in {elapsed:.3f} seconds.")


if __name__ == '__main__':
    main()
