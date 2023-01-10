# Evernote to Markdown converter

This is a simple script to convert Evernote notes to Markdown.
I decided to write a new tool from scratch because I was not satisfied with
the way single newlines and non-breaking spaces were handled by the
[Joplin importer](https://joplinapp.org/help/#importing-from-evernote) or the
[Markdownify](https://github.com/matthewwithanm/python-markdownify) script.
See *Rationale* below for more details.

The script is tailored to my notes, but I share it in case it can be useful to
someone else. The features and the generated Markdown are oriented toward the
platforms I considered at the time, namely [Obsidian](https://obsidian.md/) and
[Joplin](https://joplinapp.org).
See *Features* below for more details and  *Limitations* for the known issues.

## Setup

The only dependency is [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)
(BS4).
To install it, run:

    pip install beautifulsoup4

I would have preferred a dependency-free solution, but BS4 greatly simplifies
handling all the HTML quirks in Evernote.

## Usage

The tool requires the notes to be exported in HTML format (not ENEX) because it
is the only format that preserves the internal links.
The option is only available in the old Evernote client (6.x), which is still
available for download from the
[official website](https://help.evernote.com/hc/en-us/articles/360052560314-Install-an-older-version-of-Evernote).

The HTML files can be exported from Evernote using the *File* → *Export Notes*
menu and selecting *Export as multiple Web pages*; in *Options*, selects all
the note attributes.

I advise having unique note names to facilitate the conversion and the
preservation of internal links.
You can use a test run to check for conversion issues (see *Warnings*) and fix
formatting and links on Evernote's side before export, to reduce the amount of
manual fixing after the conversion.

To convert a single note, run:

    python exporter.py <file>

The following options are available:

* `-output <directory>`: save the converted note in the specified directory.
* `-mode <mode>`: select the conversion mode. The available modes are:

  * `all`: convert all the notes in the directory.
  * `single`: convert a single note (default).
  * `recursive`: given a note, recursively convert all the notes linked by it.

* `-verbose`: verbosity level. The default is 0, which means only warnings and
  errors are printed. The higher the level, the more information is printed.
* `-test`: test mode. The converted notes are not saved to disk.

By default, the notes are converted to a file in the same directory. To convert
all the notes in a directory, and save the result in a different directory, run:

    python exporter.py <directory> -m all -o <output directory>

A list of note roots (by following internal links) is printed at the end to
identify the note clusters in the converted notes.

To convert the notes recursively, run:

    python exporter.py <file> -m recursive -o <output directory>

In this case, the list of notes in the directory but not converted is printed
at the end.

## Rationale

My notes rely on a lot of formatting cues, such as single newlines, underline
and indentation with non-breaking spaces to convey meaning.
Most Evernote importers, such as Joplin's or [Notion](https://www.notion.so/evernote)'s,
ignore a lot of formatting details, including converting all newlines as
paragraph breaks and dropping non-breaking spaces and color information during
conversion, making the notes harder to read.
These issues are shared by other standalone tools, such as
[Markdownify](https://github.com/matthewwithanm/python-markdownify).

Moreover, the HTML code in Evernote notes has a lot of quirks, especially in
old notes, because the application went through several rounds of updates and
note conversions.
For example, the `font-family` attribute may be replaced by a cryptic `{`, or
old formatting tags. such as `<b>`, may be used instead of the more modern
spans with formatting attributes, especially in notes clipped from the web.

## Features

The script supports italics, bold, strikethrough, code, and code blocks with
Markdown syntax and underline, superscript, subscript, abbreviations, colors
and text alignment with HTML tags.
The attributes are normalized during the conversion, e.g. `<i>` and
`font-style:italic` are converted to the same Markdown syntax.
The script also handles tables conversion, including multi-row and multi-column
cells, cell alignment, multi-line text, and images.

The notes attributes are saved in the Markdown frontmatter.
The following attributes are supported: *title*, *creation date*,
*modification date*, *tags*, *source URL*, *location* and *author*.

Attached media and files are stored in a directory `<note name>_files` under
`resources`.
Files are included in the notes without the full path, following Obsidian's
strategy of linking files implicitly as long as they have a unique name.

The script supports the following options, specified as constants:

* `USE_WIKILINKS`: use Wikipedia-style links instead of Markdown links for
  internal links (default: true).
* `MD_DEFINITIONS`: use the Multi-Markdown syntax for definitions lists instead
  of HTML tags (default: false).
* `INLINE_PREVIEWS`: how to handle media embedding. The options are:

  * `none`: do not embed and use a link with a placeholder image (like
    Evernote).
  * `link`: use a Markdown link to the media, which will be automatically
    previewed; this is Joplin's strategy.
  * `images`: use the transclusion syntax (`![[<file.ext>]]`) to embed the
    media; this is Obsidian's strategy (default).

* `IMAGE_MODE`: how to handle images. The options are:

  * `html`: use the HTML `<img>` tag; this is required for Joplin, because it
    does not support the Markdown syntax for images.
  * `markdown`: use the Markdown syntax for images, i.e., `![alt txt](img.ext)`.
  * `wiki`: transclusion syntax (default); in Obsidian, this is the only method
    that allows specifying the dimensions of the image.

The default values work best for Obsidian. For Joplin, set `USE_WIKILINKS` to
`false`, `MD_DEFINITIONS` to `true`, `INLINE_PREVIEWS` to `link` and
`IMAGE_MODE` to `html`.

### Warnings

The script flags anomalous conditions with a warning, such as:

* **empty links**: occasionally, Evernote drops a link and leaves only a
  `https` prefix; the script removes the link and prints a warning.
* **unresolved internal links**: an internal link is left in the form
  `evernote://hash`; it may be a now invalid link, but sometimes the exporter
  simply fails to replace it.
* **external images**: an image has a remote URL; while the note renders
  correctly, you may want all the images downloaded locally.
* **invalid \<dt\>**: a non-standard definition list starting with the
  definition text; it is not supported in Markdown and is always kept in HTML.

## Limitations

I designed the script to automate as much as possible the conversion of my
notes, but I left some edge cases to be fixed manually after the conversion.

### Formatting

Evernote often uses several nested `<span>` tags even for simple
formatting, often because several rounds of formatting from the WYSIWYG
interface, which hides the underlying tags, left a patchwork of styles.
The script attempts to merge text with the same format attributes, but a
complete solution would require tracking tags across different levels of the
hierarchy.
I opted for a simpler solution, shared attributes in a group of tags are merged
and applied at the end, while only the differential attributes are propagated
to the Markdown generation stage.
This handles cases with a 1-level nesting, such as `<sub>` and `<sup>` tags
inside bold or italic text, but not more complex cases.

### Bullet points

Evernote may use different combinations for nested bullet-point lists, e.g.,
using an intermediated `<li>` tag or directly nesting `<ul>` and `<ol>` tags.
Moreover, whitespace and newlines in the HTML code may affect the final output.
The heuristics in the script work most of the time, but in some cases,
especially with empty list elements or newlines between tags, you may need some
manual correction, e.g., to remove additional newlines.

### Checkboxes

Evernote unfortunately exports the checkboxes as images with non-descriptive
names (*input\_1*, etc.) making a direct conversion to Markdown impossible.
While a simple image-matching heuristic could automate the conversion, I
preferred to leave the checkboxes as they are.
You may replace the images with Markdown checkboxes at the start of a line or
use the `<input type="checkbox">` HTML tag with the optional "checked"
attribute in the middle of a line.

### Note titles

When exporting notes as HTML files, Evernote limits the length of the file name
to 50 characters, including the extension, and drops the rest.
Moreover, it drops special characters and characters not allowed in a file name
in Windows and/or Linux, such as slashes.
You need to edit the file name manually to restore the title and adjust the
`title` attribute in the frontmatter when characters are not allowed in the file
name.

### Notebooks

When the notes are exported as HTML files, Evernote does not include the
notebook name in the file name, so the script cannot automatically sort the
notes in subdirectories.
The workaround is to manually add to all the notes in a notebook a tag with the
notebook path prepended by `nb:`, i.e., `nb:collection/notebook`; the script
will automatically create the subdirectories and strip the tags from the notes.

## Rewrite file dates

While Joplin honors the creation and modification dates in the frontmatter,
Obsidian only relies on the information stored in the filesystem.
To align the dates with the Evernote notes, I wrote a `update_file_dates`
script to read the creation and modification dates from the frontmatter and
update the file attributes accordingly.
The script is available for Windows Powershell (extension `.ps1`) and for
Linux / Mac OS X bash (extension `.sh`). In both cases, it accepts a directory
as an argument to restrict the update to the files in the directory; by default,
it updates all files in the current directory and subdirectories.

Launch the script under Windows with the following command, to bypass the
execution restrictions:

    powershell -ExecutionPolicy Bypass -File .\update_file_dates.ps1 <path>

On Linux / Mac OS X, simply run:

    sh update_file_dates.sh <path>

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE.txt)
file.
