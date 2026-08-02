from __future__ import annotations

from textual.widget import Widget

from groundskeeping.contracts import (
    EmptyView,
    NavigationItem,
    PageContext,
    PageNavigation,
    PageRoute,
    SectionNavigation,
    SurfaceView,
)


class GroundworkersPage(Widget):
    route: PageRoute

    def __init__(self, route: PageRoute) -> None:
        super().__init__()
        self.route = route

    def activate(self, context: PageContext) -> None:
        return None

    def deactivate(self, context: PageContext) -> None:
        return None

    def build_navigation(self, context: PageContext) -> PageNavigation:
        return SectionNavigation(items=())

    def landing_view(self, context: PageContext) -> SurfaceView:
        return EmptyView(title=self.route.label, message="No content available.")

    def navigation_selected(self, item: NavigationItem, context: PageContext) -> None:
        context.surface.show_view(self.route.key, self.landing_view(context))

    def action_selected(self, action_key: str, context: PageContext) -> None:
        return None

    def row_highlighted(self, row_key: str, context: PageContext) -> None:
        return None

    def row_selected(self, row_key: str, context: PageContext) -> None:
        return None
