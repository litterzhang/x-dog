"""TUI component package.

Re-exports all built-in components for convenient imports.
"""

from tui.components.box import Box, BoxComponent
from tui.components.cancellable_loader import CancellableLoader
from tui.components.container import Container
from tui.components.editor import Editor, EditorComponent, EditorOptions, EditorTheme
from tui.components.image import Image, ImageComponent, ImageOptions, ImageTheme
from tui.components.input import Input, InputComponent
from tui.components.loader import Loader, LoaderComponent
from tui.components.markdown import Markdown, MarkdownComponent
from tui.components.select_list import SelectListComponent
from tui.components.settings_list import SettingItem, SettingsList, SettingsListTheme
from tui.components.spacer import Spacer, SpacerComponent
from tui.components.text import Text, TextComponent
from tui.components.truncated_text import TruncatedTextComponent

__all__ = [
    "Box",
    "BoxComponent",
    "CancellableLoader",
    "Container",
    "Editor",
    "EditorComponent",
    "EditorOptions",
    "EditorTheme",
    "Image",
    "ImageComponent",
    "ImageOptions",
    "ImageTheme",
    "Input",
    "InputComponent",
    "Loader",
    "LoaderComponent",
    "Markdown",
    "MarkdownComponent",
    "SelectListComponent",
    "SettingItem",
    "SettingsList",
    "SettingsListTheme",
    "Spacer",
    "SpacerComponent",
    "Text",
    "TextComponent",
    "TruncatedTextComponent",
]
