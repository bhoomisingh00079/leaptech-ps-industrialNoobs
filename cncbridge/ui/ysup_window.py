"""YSUP-Link listener settings, isolated as an optional App extension."""
import tkinter as tk
from tkinter import ttk

from .. import theme
from ..i18n import tr
from .scrollframe import ScrollFrame


class YsupExtension:
    """Admin-only configuration for the receiver; the Engine owns its runtime lifecycle."""
    def __init__(self, app):
        self.app = app
        self.enabled = tk.BooleanVar(app)
        self.address = tk.StringVar(app)
        self.port = tk.StringVar(app)
        self.username = tk.StringVar(app)
        self.password = tk.StringVar(app)
        self.token = tk.StringVar(app)

    def option_pages(self):
        page = ScrollFrame(self.app.options_tabs, theme.BACKGROUND, padding=self.app.L.tab_pad)
        self.app.options_tabs.add(page, text=f"  {tr('YSUP-Link')}  ")
        self.app.option_pages["ysup"] = page
        box = ttk.LabelFrame(page.body, text=tr(" YSUP-Link receiver "), padding=self.app.L.box_pad)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Checkbutton(box, text=tr("Enable receiver while monitoring"), variable=self.enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(box, text=tr("Listen address"), style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.address).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4)
        ttk.Label(box, text=tr("Port"), style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.port, width=10).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=4)
        ttk.Label(box, text=tr("Basic Auth user name"), style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.username).grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=4)
        ttk.Label(box, text=tr("Basic Auth password"), style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.password, show="*").grid(row=4, column=1, sticky="ew", padx=(10, 0), pady=4)
        ttk.Label(box, text=tr("API token"), style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.token, show="*").grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=4)
        ttk.Label(box, text=tr("Use Basic Auth or a bearer/X-YSUP-Token value. Credentials are encrypted with the "
                               "other saved secrets. The default address accepts local requests only; expose "
                               "0.0.0.0 only when firewall and credentials are configured."),
                  style="CardSmall.TLabel", wraplength=self.app.px(600), justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(page.body, text=tr("The receiver starts and stops with monitoring. YSUP-Link may POST to / "
                                     "or /ysup/api."), style="PageHint.TLabel", wraplength=self.app.px(610),
                  justify="left").pack(anchor="w", pady=(self.app.L.box_gap, 0))

    def fill(self, cfg):
        self.enabled.set(cfg["ysup_enabled"])
        self.address.set(cfg["ysup_listen_address"])
        self.port.set(str(cfg["ysup_port"]))
        self.username.set(cfg["ysup_basic_username"])
        self.password.set(cfg["ysup_basic_password"])
        self.token.set(cfg["ysup_api_token"])

    def collect(self, cfg):
        cfg["ysup_enabled"] = self.enabled.get()
        cfg["ysup_listen_address"] = self.address.get()
        cfg["ysup_port"] = self.port.get()
        cfg["ysup_basic_username"] = self.username.get()
        cfg["ysup_basic_password"] = self.password.get()
        cfg["ysup_api_token"] = self.token.get()
