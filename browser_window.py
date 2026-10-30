"""Use Browse as the owner of all finder windows."""

import aqt
from aqt import mw


def browser_window(browser=None):
    if browser is None or browser is mw:
        browser = aqt.dialogs.open("Browser", mw)
    if browser.isMinimized():
        browser.showNormal()
    browser.raise_()
    browser.activateWindow()
    return browser
