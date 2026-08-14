from dataclasses import dataclass, field


@dataclass
class MenuItem:
    label: str
    url: str
    order: int = 50


@dataclass
class WidgetDefinition:
    key: str
    title: str
    endpoint: str
    order: int = 50


class ExtensionRegistry:
    """Minimal module/extension registry for menu and dashboard contributions."""

    def __init__(self) -> None:
        self.menus: dict[str, list[MenuItem]] = {}
        self.widgets: dict[str, WidgetDefinition] = {}
        self.modules: dict[str, str] = {}

    def register_module(self, name: str, display_name: str) -> None:
        self.modules[name] = display_name

    def add_menu_item(self, group: str, label: str, url: str, order: int = 50) -> None:
        self.menus.setdefault(group, []).append(MenuItem(label=label, url=url, order=order))

    def add_widget(self, key: str, title: str, endpoint: str, order: int = 50) -> None:
        self.widgets[key] = WidgetDefinition(key=key, title=title, endpoint=endpoint, order=order)

    def menu_items(self, group: str) -> list[MenuItem]:
        return sorted(self.menus.get(group, []), key=lambda item: item.order)

    def all_widgets(self) -> list[WidgetDefinition]:
        return sorted(self.widgets.values(), key=lambda item: item.order)


extension_registry = ExtensionRegistry()
