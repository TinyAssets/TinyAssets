"""Tray, dashboard, notification, and icon helpers used by the domain runner.

Re-exports
----------
TrayApp              -- pystray system tray icon (run_detached)
DashboardHandler     -- processes graph stream events for display
NotificationManager  -- toast / balloon notifications
create_icon_image    -- generate a branded icon PIL Image
generate_icon        -- generate multi-size .ico file
"""

from tinyassets.desktop.dashboard import DashboardHandler, DashboardMetrics
from tinyassets.desktop.icon_gen import create_icon_image, generate_icon
from tinyassets.desktop.notifications import NotificationManager
from tinyassets.desktop.tray import TrayApp

__all__ = [
    "DashboardHandler",
    "DashboardMetrics",
    "NotificationManager",
    "TrayApp",
    "create_icon_image",
    "generate_icon",
]
