"""Shared helpers for local visual automation tools."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import plistlib
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..clock import now_ms
from ..config import get_settings
from ..file_intake import safe_filename
from ..ids import uid
from ..schema import Artifact, ArtifactVersion


_SCROLL_KEY_CODES: dict[tuple[str, str], int] = {
    ("down", "page"): 121,
    ("up", "page"): 116,
    ("down", "line"): 125,
    ("up", "line"): 126,
    ("right", "page"): 124,
    ("left", "page"): 123,
    ("right", "line"): 124,
    ("left", "line"): 123,
}
_VISUAL_PROCESS_TOOLS = {
    "browser.profiles",
    "browser.open",
    "browser.snapshot",
    "browser.act",
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.screenshot",
    "desktop.apps",
    "desktop.snapshot",
    "desktop.act",
    "desktop.open_app",
    "desktop.activate_app",
    "desktop.quit_app",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "notify.send",
}
_DESKTOP_REF_MAX_AGE_MS = 5 * 60 * 1000
_USER_BROWSER_PROFILE_ALIASES = {"", "user", "default", "host", "personal"}
_OWN_BROWSER_PROFILE_ALIASES = {"atrium", "own", "agent", "system", "isolated"}
_BROWSER_PROFILE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$")
_BROWSER_APP_CANDIDATES = [
    ("Google Chrome", Path("/Applications/Google Chrome.app")),
    ("Google Chrome", Path.home() / "Applications/Google Chrome.app"),
    ("Brave Browser", Path("/Applications/Brave Browser.app")),
    ("Brave Browser", Path.home() / "Applications/Brave Browser.app"),
    ("Microsoft Edge", Path("/Applications/Microsoft Edge.app")),
    ("Microsoft Edge", Path.home() / "Applications/Microsoft Edge.app"),
    ("Chromium", Path("/Applications/Chromium.app")),
    ("Chromium", Path.home() / "Applications/Chromium.app"),
    ("Google Chrome Canary", Path("/Applications/Google Chrome Canary.app")),
    ("Google Chrome Canary", Path.home() / "Applications/Google Chrome Canary.app"),
]

_BROWSER_PLAYWRIGHT_HELPER_SOURCE = r'''
const fs = require('fs');
const { createRequire } = require('module');
const path = require('path');

function clip(value, limit) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? text.slice(0, Math.max(0, limit - 1)) + '...' : text;
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (_error) {
    return {};
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}

function parseUpdatedAtMs(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return NaN;
}

function normalizeUrl(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  try {
    return new URL(text).href;
  } catch (_error) {
    return text;
  }
}

function validateBrowserRefState(input, state, refInfo) {
  if (!input.ref) return;
  if (!refInfo) {
    throw new Error('browser.act requires selector or a ref from the latest browser.snapshot result');
  }
  if (state.profile && state.profile !== input.profile) {
    throw new Error(`browser.act ref was captured for profile ${state.profile}; call browser.snapshot again for profile ${input.profile}`);
  }
  if (state.platform && state.platform !== process.platform) {
    throw new Error(`browser.act ref was captured on ${state.platform}; call browser.snapshot again on ${process.platform}`);
  }
  const requestedUrl = normalizeUrl(input.url);
  if (requestedUrl) {
    const stateUrl = normalizeUrl(state.lastUrl);
    if (!stateUrl) {
      throw new Error('browser.act ref URL is unknown; call browser.snapshot again');
    }
    if (stateUrl !== requestedUrl) {
      throw new Error(`browser.act ref was captured for ${stateUrl}; call browser.snapshot again for ${requestedUrl}`);
    }
  }
  if (input.allowStaleRef) return;
  const updatedAtMs = parseUpdatedAtMs(state.updatedAtMs || state.updatedAt);
  if (!Number.isFinite(updatedAtMs)) {
    throw new Error('browser.act ref age is unknown; call browser.snapshot again');
  }
  const maxRefAgeMs = Math.max(1000, Math.min(Number(input.maxRefAgeMs || 300000), 3600000));
  if ((Date.now() - updatedAtMs) > maxRefAgeMs) {
    throw new Error('browser.act ref is stale; call browser.snapshot again');
  }
}

function mismatchMessage(field, expected, actual) {
  return `browser.act ref ${field} changed from ${expected || '(empty)'} to ${actual || '(empty)'}; call browser.snapshot again`;
}

function normalizeIdentityText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function elementEnabled(el) {
  return !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
}

function elementHref(el) {
  return el.tagName.toLowerCase() === 'a' ? normalizeUrl(el.href || '') || undefined : undefined;
}

async function currentBrowserElementIdentity(locator) {
  return await locator.evaluate((el) => {
    function clip(value, limit) {
      const text = String(value || '').replace(/\s+/g, ' ').trim();
      return text.length > limit ? text.slice(0, Math.max(0, limit - 1)) + '...' : text;
    }

    function roleFor(el) {
      const explicit = (el.getAttribute('role') || '').trim();
      if (explicit) return explicit;
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (tag === 'a' && el.hasAttribute('href')) return 'link';
      if (tag === 'button') return 'button';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'summary') return 'button';
      if (tag === 'img') return 'img';
      if (tag === 'input') {
        if (['button', 'submit', 'reset'].includes(type)) return 'button';
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'range') return 'slider';
        if (type === 'search') return 'searchbox';
        return 'textbox';
      }
      return tag;
    }

    function labelledBy(el) {
      const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
      return ids.map((id) => document.getElementById(id)).filter(Boolean).map((node) => node.textContent || '').join(' ');
    }

    function labelsFor(el) {
      if (!el.labels) return '';
      return Array.from(el.labels).map((label) => label.textContent || '').join(' ');
    }

    function nameFor(el) {
      const aria = el.getAttribute('aria-label') || labelledBy(el);
      if (aria) return clip(aria, 160);
      const tag = el.tagName.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') {
        const label = labelsFor(el);
        if (label) return clip(label, 160);
        const placeholder = el.getAttribute('placeholder');
        if (placeholder) return clip(placeholder, 160);
      }
      const alt = el.getAttribute('alt');
      if (alt) return clip(alt, 160);
      const title = el.getAttribute('title');
      if (title) return clip(title, 160);
      return clip(el.textContent || '', 160);
    }

    function normalizeUrl(value) {
      const text = String(value || '').trim();
      if (!text) return '';
      try {
        return new URL(text).href;
      } catch (_error) {
        return text;
      }
    }

    function elementEnabled(el) {
      return !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
    }

    function elementHref(el) {
      return el.tagName.toLowerCase() === 'a' ? normalizeUrl(el.href || '') || undefined : undefined;
    }

    return {
      tag: el.tagName.toLowerCase(),
      role: roleFor(el),
      name: nameFor(el),
      enabled: elementEnabled(el),
      checked: typeof el.checked === 'boolean' ? el.checked : undefined,
      href: elementHref(el)
    };
  });
}

async function validateBrowserRefElement(page, selector, refInfo) {
  if (!refInfo) return page.locator(selector).first();
  const locator = page.locator(selector);
  const count = await locator.count();
  if (count !== 1) {
    throw new Error(`browser.act ref selector matched ${count} elements; call browser.snapshot again`);
  }
  const first = locator.first();
  const current = await currentBrowserElementIdentity(first);
  for (const field of ['tag', 'role', 'name']) {
    const expected = normalizeIdentityText(refInfo[field]);
    const actual = normalizeIdentityText(current[field]);
    if (expected && expected !== actual) {
      throw new Error(mismatchMessage(field, expected, actual));
    }
  }
  if (typeof refInfo.enabled === 'boolean') {
    if (current.enabled !== refInfo.enabled) {
      throw new Error(mismatchMessage('enabled', String(refInfo.enabled), String(current.enabled)));
    }
    if (current.enabled === false) {
      throw new Error('browser.act ref is disabled; call browser.snapshot again');
    }
  }
  if (typeof refInfo.checked === 'boolean' && typeof current.checked === 'boolean' && current.checked !== refInfo.checked) {
    throw new Error(mismatchMessage('checked', String(refInfo.checked), String(current.checked)));
  }
  const expectedHref = normalizeUrl(refInfo.href);
  const actualHref = normalizeUrl(current.href);
  if (expectedHref && expectedHref !== actualHref) {
    throw new Error(mismatchMessage('href', expectedHref, actualHref));
  }
  return first;
}

function loadPlaywright(input) {
  const errors = [];
  const packageNames = ['playwright', '@playwright/test'];
  for (const packageName of packageNames) {
    try {
      return require(packageName);
    } catch (error) {
      errors.push(`${packageName}: ${error && error.message ? error.message : String(error)}`);
    }
  }
  for (const root of [input.requireFrom, process.cwd()].filter(Boolean)) {
    const req = createRequire(path.join(root, 'package.json'));
    for (const packageName of packageNames) {
      try {
        return req(packageName);
      } catch (error) {
        errors.push(`${root}:${packageName}: ${error && error.message ? error.message : String(error)}`);
      }
    }
  }
  throw new Error(`Playwright is required for browser.snapshot/browser.act. Tried playwright and @playwright/test from local helper, requireFrom, and cwd. ${errors.join(' | ')}`);
}

async function maybeWait(page, state, timeoutMs) {
  try {
    await page.waitForLoadState(state, { timeout: timeoutMs });
  } catch (_error) {
    // Some apps keep network connections open. A snapshot is still useful.
  }
}

async function collectSnapshot(page, options) {
  const title = await page.title();
  const url = page.url();
  const dom = await page.evaluate((opts) => {
    function clip(value, limit) {
      const text = String(value || '').replace(/\s+/g, ' ').trim();
      return text.length > limit ? text.slice(0, Math.max(0, limit - 1)) + '...' : text;
    }

    function cssEscape(value) {
      if (window.CSS && typeof window.CSS.escape === 'function') {
        return window.CSS.escape(String(value));
      }
      return String(value).replace(/[^a-zA-Z0-9_-]/g, (char) => '\\' + char);
    }

    function isVisible(el) {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0;
    }

    function elementEnabled(el) {
      return !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
    }

    function elementHref(el) {
      return el.tagName.toLowerCase() === 'a' ? el.href || undefined : undefined;
    }

    function roleFor(el) {
      const explicit = (el.getAttribute('role') || '').trim();
      if (explicit) return explicit;
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (tag === 'a' && el.hasAttribute('href')) return 'link';
      if (tag === 'button') return 'button';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'summary') return 'button';
      if (tag === 'img') return 'img';
      if (tag === 'input') {
        if (['button', 'submit', 'reset'].includes(type)) return 'button';
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'range') return 'slider';
        if (type === 'search') return 'searchbox';
        return 'textbox';
      }
      return tag;
    }

    function labelledBy(el) {
      const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
      return ids.map((id) => document.getElementById(id)).filter(Boolean).map((node) => node.textContent || '').join(' ');
    }

    function labelsFor(el) {
      if (!el.labels) return '';
      return Array.from(el.labels).map((label) => label.textContent || '').join(' ');
    }

    function nameFor(el) {
      const aria = el.getAttribute('aria-label') || labelledBy(el);
      if (aria) return clip(aria, 160);
      const tag = el.tagName.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') {
        const label = labelsFor(el);
        if (label) return clip(label, 160);
        const placeholder = el.getAttribute('placeholder');
        if (placeholder) return clip(placeholder, 160);
      }
      const alt = el.getAttribute('alt');
      if (alt) return clip(alt, 160);
      const title = el.getAttribute('title');
      if (title) return clip(title, 160);
      return clip(el.textContent || '', 160);
    }

    function uniqueSelector(el) {
      const id = el.getAttribute('id');
      if (id) {
        const selector = '#' + cssEscape(id);
        if (document.querySelectorAll(selector).length === 1) return selector;
      }
      for (const attr of ['data-testid', 'data-test', 'data-cy', 'name', 'aria-label']) {
        const value = el.getAttribute(attr);
        if (!value) continue;
        const selector = `${el.tagName.toLowerCase()}[${attr}="${String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`;
        try {
          if (document.querySelectorAll(selector).length === 1) return selector;
        } catch (_error) {
          // Fall through to a structural selector.
        }
      }
      const parts = [];
      let node = el;
      while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.documentElement) {
        const tag = node.tagName.toLowerCase();
        let index = 1;
        let sibling = node.previousElementSibling;
        while (sibling) {
          if (sibling.tagName.toLowerCase() === tag) index += 1;
          sibling = sibling.previousElementSibling;
        }
        parts.unshift(`${tag}:nth-of-type(${index})`);
        const selector = parts.join(' > ');
        try {
          if (document.querySelectorAll(selector).length === 1) return selector;
        } catch (_error) {
          // Keep walking.
        }
        node = node.parentElement;
      }
      return parts.join(' > ');
    }

    const selector = [
      'a[href]',
      'button',
      'input:not([type="hidden"])',
      'textarea',
      'select',
      'summary',
      '[role]',
      '[contenteditable="true"]',
      '[tabindex]:not([tabindex="-1"])'
    ].join(',');
    const elements = [];
    const seen = new Set();
    for (const el of Array.from(document.querySelectorAll(selector))) {
      if (seen.has(el) || !isVisible(el)) continue;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      const ref = `b${elements.length + 1}`;
      elements.push({
        ref,
        role: roleFor(el),
        name: nameFor(el),
        selector: uniqueSelector(el),
        tag: el.tagName.toLowerCase(),
        enabled: elementEnabled(el),
        checked: typeof el.checked === 'boolean' ? el.checked : undefined,
        href: elementHref(el),
        bbox: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height)
        }
      });
      if (elements.length >= opts.maxElements) break;
    }
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      text: opts.includeText ? clip(document.body ? document.body.innerText : '', opts.maxTextChars) : undefined,
      elements
    };
  }, {
    includeText: Boolean(options.includeText),
    maxElements: options.maxElements,
    maxTextChars: options.maxTextChars
  });
  return { title, url, ...dom };
}

async function main() {
  const input = JSON.parse(process.argv[2] || '{}');
  const { chromium } = loadPlaywright(input);
  const timeoutMs = Math.max(1000, Math.min(Number(input.timeoutMs || 15000), 120000));
  const userDataDir = input.userDataDir;
  const statePath = path.join(userDataDir, '.atrium-browser-state.json');
  const state = readJson(statePath);
  const launchOptions = {
    headless: Boolean(input.headless),
    viewport: input.viewport || { width: 1280, height: 720 },
    args: ['--no-first-run', '--no-default-browser-check']
  };
  if (input.executablePath) {
    launchOptions.executablePath = input.executablePath;
  }
  const context = await chromium.launchPersistentContext(userDataDir, launchOptions);
  try {
    let page = context.pages()[0] || await context.newPage();
    let refInfo = null;
    if (input.mode === 'act' && input.ref) {
      refInfo = state.refs ? state.refs[input.ref] : null;
      validateBrowserRefState(input, state, refInfo);
    }
    const targetUrl = input.url || state.lastUrl;
    if (targetUrl && page.url() !== targetUrl) {
      await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
    }
    await maybeWait(page, 'networkidle', Math.min(timeoutMs, 3000));

    let actionResult = null;
    if (input.mode === 'act') {
      const selector = input.selector || (refInfo && refInfo.selector);
      if (!selector) {
        throw new Error('browser.act requires selector or a ref from the latest browser.snapshot result');
      }
      const action = String(input.action || 'click').toLowerCase();
      const locator = await validateBrowserRefElement(page, selector, refInfo);
      await locator.waitFor({ state: 'visible', timeout: timeoutMs });
      if (action === 'click') {
        await locator.click({ timeout: timeoutMs });
      } else if (action === 'fill') {
        await locator.fill(String(input.text ?? input.value ?? ''), { timeout: timeoutMs });
      } else if (action === 'type') {
        await locator.type(String(input.text ?? input.value ?? ''), { timeout: timeoutMs });
      } else if (action === 'press') {
        await locator.press(String(input.key || 'Enter'), { timeout: timeoutMs });
      } else if (action === 'check') {
        await locator.check({ timeout: timeoutMs });
      } else if (action === 'uncheck') {
        await locator.uncheck({ timeout: timeoutMs });
      } else if (action === 'select') {
        await locator.selectOption(input.value, { timeout: timeoutMs });
      } else if (action === 'hover') {
        await locator.hover({ timeout: timeoutMs });
      } else {
        throw new Error(`unsupported browser.act action: ${action}`);
      }
      await page.waitForTimeout(Math.max(0, Math.min(Number(input.waitAfterMs || 250), 5000)));
      await maybeWait(page, 'domcontentloaded', Math.min(timeoutMs, 3000));
      await maybeWait(page, 'networkidle', Math.min(timeoutMs, 3000));
      actionResult = { action, ref: input.ref || null, selector };
    }

    const snapshot = await collectSnapshot(page, {
      includeText: input.includeText !== false,
      maxElements: Math.max(1, Math.min(Number(input.maxElements || 80), 300)),
      maxTextChars: Math.max(0, Math.min(Number(input.maxTextChars || 12000), 60000))
    });
    const refs = {};
    for (const element of snapshot.elements || []) {
      refs[element.ref] = {
        selector: element.selector,
        role: element.role,
        name: element.name,
        tag: element.tag,
        enabled: element.enabled,
        checked: element.checked,
        href: element.href
      };
    }
    writeJson(statePath, {
      lastUrl: snapshot.url,
      title: snapshot.title,
      profile: input.profile,
      platform: process.platform,
      refs,
      updatedAt: new Date().toISOString(),
      updatedAtMs: Date.now()
    });
    console.log(JSON.stringify({
      returnCode: 0,
      ok: true,
      backend: 'playwright',
      profile: input.profile,
      profileKind: 'isolated',
      isOwnProfile: input.profile === 'atrium',
      userDataDir,
      url: snapshot.url,
      title: snapshot.title,
      refCount: snapshot.elements.length,
      action: actionResult,
      snapshot
    }));
  } finally {
    await context.close();
  }
}

main().then(() => {
  process.exit(0);
}).catch((error) => {
  console.log(JSON.stringify({
    returnCode: 1,
    ok: false,
    backend: 'playwright',
    stderr: error && error.stack ? error.stack : String(error)
  }));
  process.exit(1);
});
'''


def _windows_browser_candidates() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    standard_roots = [
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
    ]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        root = Path(base)
        if root not in standard_roots:
            standard_roots.append(root)
    for root in standard_roots:
        candidates.extend([
            ("Google Chrome", root / "Google/Chrome/Application/chrome.exe"),
            ("Microsoft Edge", root / "Microsoft/Edge/Application/msedge.exe"),
            ("Brave Browser", root / "BraveSoftware/Brave-Browser/Application/brave.exe"),
            ("Chromium", root / "Chromium/Application/chrome.exe"),
        ])
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data)
        candidates.extend([
            ("Google Chrome", root / "Google/Chrome/Application/chrome.exe"),
            ("Microsoft Edge", root / "Microsoft/Edge/Application/msedge.exe"),
            ("Brave Browser", root / "BraveSoftware/Brave-Browser/Application/brave.exe"),
        ])
    return candidates


def _windows_start_menu_dirs() -> list[Path]:
    dirs: list[Path] = []
    program_data = os.environ.get("ProgramData")
    app_data = os.environ.get("APPDATA")
    if program_data:
        dirs.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
    if app_data:
        dirs.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
    return dirs


_APP_SEARCH_DIRS = [
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
]
_WINDOWS_POWERSHELL_CANDIDATES = (
    "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "C:/Windows/SysWOW64/WindowsPowerShell/v1.0/powershell.exe",
    "C:/Program Files/PowerShell/7/pwsh.exe",
    "C:/Program Files (x86)/PowerShell/7/pwsh.exe",
)

_WINDOWS_VISUAL_HELPER_SOURCE = r'''
import ctypes
import json
import sys
import time

user32 = ctypes.windll.user32

def _configure_user32_signatures():
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware.argtypes = ()
        user32.SetProcessDPIAware.restype = ctypes.c_bool
    except Exception:
        pass
    try:
        user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        user32.SetCursorPos.restype = ctypes.c_bool
    except Exception:
        pass
    try:
        user32.VkKeyScanW.argtypes = (ctypes.c_wchar,)
        user32.VkKeyScanW.restype = ctypes.c_short
    except Exception:
        pass
    try:
        user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
        user32.GetSystemMetrics.restype = ctypes.c_int
    except Exception:
        pass

_configure_user32_signatures()
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
WHEEL_DELTA = 120
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

def _enable_dpi_awareness():
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per_monitor_v2"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unverified"

_DPI_AWARENESS = _enable_dpi_awareness()
_EXTENDED_KEY_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x6F}

def _payload():
    return json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")

def _ok(**extra):
    print(json.dumps({"ok": True, "dpiAwareness": _DPI_AWARENESS, **extra}, separators=(",", ":")))

def _fail(message, **extra):
    print(json.dumps({"ok": False, "error": str(message), "dpiAwareness": _DPI_AWARENESS, **extra}, separators=(",", ":")))
    raise SystemExit(1)

def _virtual_bounds():
    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    return left, top, width, height

def _assert_point_in_virtual_screen(x, y):
    left, top, width, height = _virtual_bounds()
    if width <= 0 or height <= 0:
        return
    if int(x) < left or int(y) < top or int(x) >= left + width or int(y) >= top + height:
        raise OSError(f"coordinates outside virtual screen bounds: {x},{y} not in {left},{top},{width}x{height}")

def _send_vk(vk, down=True):
    flags = KEYEVENTF_EXTENDEDKEY if int(vk) in _EXTENDED_KEY_VKS else 0
    if not down:
        flags |= KEYEVENTF_KEYUP
    _send_key_event(vk, flags=flags)

def _click():
    data = _payload()
    x = int(float(data.get("x")))
    y = int(float(data.get("y")))
    button = str(data.get("button") or "left").lower()
    _assert_point_in_virtual_screen(x, y)
    if not user32.SetCursorPos(x, y):
        raise OSError("SetCursorPos failed")
    time.sleep(0.03)
    events = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    }
    down, up = events.get(button, events["left"])
    _send_mouse_event(down)
    time.sleep(0.05)
    _send_mouse_event(up)
    _ok(mode="click", x=x, y=y, button=button, inputMethod="sendinput")

def _keypress():
    data = _payload()
    raw_keys = data.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise SystemExit("keypress requires keys list")
    normalized = [str(item).strip().lower() for item in raw_keys if str(item).strip()]
    modifier_aliases = {
        "ctrl": "control",
        "control": "control",
        "cmd": "control",
        "command": "control",
        "meta": "control",
        "shift": "shift",
        "alt": "alt",
        "option": "alt",
        "win": "win",
        "windows": "win",
        "super": "win",
    }
    modifier_vks = {"control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
    modifiers = [modifier_aliases[item] for item in normalized if item in modifier_aliases]
    key_parts = [item for item in normalized if item not in modifier_aliases]
    if len(key_parts) != 1:
        raise SystemExit("keypress requires exactly one non-modifier key")
    aliases = {
        "enter": "return",
        "esc": "escape",
        "backspace": "delete",
        "forward_delete": "forwarddelete",
        "del": "forwarddelete",
        "ins": "insert",
        "page_down": "pagedown",
        "page up": "pageup",
        "page_up": "pageup",
        "page down": "pagedown",
    }
    key = aliases.get(key_parts[0], key_parts[0])
    special = {
        "return": 0x0D,
        "tab": 0x09,
        "space": 0x20,
        "delete": 0x08,
        "escape": 0x1B,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "pagedown": 0x22,
        "forwarddelete": 0x2E,
        "insert": 0x2D,
    }
    implicit_modifiers = []
    if key in special:
        vk = special[key]
    elif len(key) == 1:
        code = int(user32.VkKeyScanW(key))
        if code == -1:
            raise SystemExit(f"unsupported key: {key}")
        vk = code & 0xFF
        shift_state = (code >> 8) & 0xFF
        if shift_state & 1:
            implicit_modifiers.append("shift")
        if shift_state & 2:
            implicit_modifiers.append("control")
        if shift_state & 4:
            implicit_modifiers.append("alt")
    else:
        raise SystemExit(f"unsupported key: {key}")
    final_modifiers = []
    for mod in [*modifiers, *implicit_modifiers]:
        if mod not in final_modifiers:
            final_modifiers.append(mod)
    for mod in final_modifiers:
        _send_vk(modifier_vks[mod], True)
        time.sleep(0.01)
    _send_vk(vk, True)
    time.sleep(0.03)
    _send_vk(vk, False)
    for mod in reversed(final_modifiers):
        time.sleep(0.01)
        _send_vk(modifier_vks[mod], False)
    _ok(mode="keypress", key=key, modifiers=final_modifiers, inputMethod="sendinput")

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short), ("wParamH", ctypes.c_ushort)]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

def _configure_sendinput_signature():
    try:
        user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        user32.SendInput.restype = ctypes.c_uint
    except Exception:
        pass

_configure_sendinput_signature()

def _send_mouse_event(flags, data=0):
    inp = INPUT(type=0, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, int(data), flags, 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput mouse event failed")

def _send_key_event(vk, scan=0, flags=0):
    inp = INPUT(type=1, union=INPUT_UNION(ki=KEYBDINPUT(int(vk), int(scan), int(flags), 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput key event failed")

def _send_mouse_wheel(delta, horizontal=False):
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    inp = INPUT(type=0, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, int(delta), flags, 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput mouse wheel failed")

def _scroll():
    data = _payload()
    direction = str(data.get("direction") or "down").lower()
    unit = str(data.get("unit") or "page").lower()
    amount = max(1, min(int(data.get("amount") or 1), 40))
    x = data.get("x")
    y = data.get("y")
    if x is not None and y is not None:
        target_x = int(float(x))
        target_y = int(float(y))
        _assert_point_in_virtual_screen(target_x, target_y)
        if not user32.SetCursorPos(target_x, target_y):
            raise OSError("SetCursorPos failed")
        time.sleep(0.02)
    horizontal = direction in {"left", "right"}
    base_steps = 5 if unit == "page" else 1
    steps = max(1, min(amount * base_steps, 80))
    sign = 1 if direction in {"up", "right"} else -1
    delta = WHEEL_DELTA * sign
    delay = max(0.0, min(float(data.get("delayMs") or 25) / 1000.0, 0.5))
    for _ in range(steps):
        _send_mouse_wheel(delta, horizontal)
        if delay:
            time.sleep(delay)
    _ok(mode="scroll", direction=direction, unit=unit, amount=amount, steps=steps, wheelDelta=delta, horizontal=horizontal, x=x, y=y, inputMethod="sendinput")

def _send_unicode_unit(unit, keyup=False):
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = INPUT(type=1, union=INPUT_UNION(ki=KEYBDINPUT(0, int(unit), flags, 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput failed")

def _type_text():
    data = _payload()
    text = data.get("text")
    if not isinstance(text, str):
        raise SystemExit("type requires text")
    raw = text.encode("utf-16-le", errors="surrogatepass")
    units = [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]
    for unit in units:
        _send_unicode_unit(unit, False)
        _send_unicode_unit(unit, True)
        time.sleep(0.003)
    _ok(mode="type", textBytes=len(text.encode("utf-8")), textCharacters=len(text), textUnits=len(units), inputMethod="sendinput")

def _selftest():
    screen_width = user32.GetSystemMetrics(0)
    screen_height = user32.GetSystemMetrics(1)
    virtual_left, virtual_top, virtual_width, virtual_height = _virtual_bounds()
    _ok(
        mode="selftest",
        screenWidth=int(screen_width),
        screenHeight=int(screen_height),
        virtualLeft=int(virtual_left),
        virtualTop=int(virtual_top),
        virtualWidth=int(virtual_width),
        virtualHeight=int(virtual_height),
    )

mode = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    if mode == "click":
        _click()
    elif mode == "keypress":
        _keypress()
    elif mode == "type":
        _type_text()
    elif mode == "scroll":
        _scroll()
    elif mode == "selftest":
        _selftest()
    else:
        raise ValueError(f"unknown mode: {mode}")
except SystemExit:
    raise
except Exception as exc:
    _fail(exc, mode=mode)
'''

_CLICK_HELPER_SOURCE = r'''
import CoreGraphics
import Foundation

let args = CommandLine.arguments
if args.count < 3 {
    FileHandle.standardError.write(Data("usage: macos_click X Y [left|right]\n".utf8))
    exit(64)
}
guard let x = Double(args[1]), let y = Double(args[2]) else {
    FileHandle.standardError.write(Data("x and y must be numbers\n".utf8))
    exit(64)
}
let rawButton = args.count >= 4 ? args[3].lowercased() : "left"
let button: CGMouseButton = rawButton == "right" ? .right : .left
let downType: CGEventType = rawButton == "right" ? .rightMouseDown : .leftMouseDown
let upType: CGEventType = rawButton == "right" ? .rightMouseUp : .leftMouseUp
let point = CGPoint(x: x, y: y)
let source = CGEventSource(stateID: .hidSystemState)

guard let down = CGEvent(mouseEventSource: source, mouseType: downType, mouseCursorPosition: point, mouseButton: button),
      let up = CGEvent(mouseEventSource: source, mouseType: upType, mouseCursorPosition: point, mouseButton: button) else {
    FileHandle.standardError.write(Data("failed to create mouse events\n".utf8))
    exit(70)
}
down.post(tap: .cghidEventTap)
usleep(50000)
up.post(tap: .cghidEventTap)
'''

_KEY_HELPER_SOURCE = r'''
import CoreGraphics
import Foundation

let keyCodes: [String: CGKeyCode] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "return": 36, "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "tab": 48, "space": 49, "`": 50, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "home": 115, "pageup": 116, "page_up": 116, "forwarddelete": 117, "end": 119, "pagedown": 121, "page_down": 121,
    "left": 123, "right": 124, "down": 125, "up": 126
]

func flags(_ modifiers: [String]) -> CGEventFlags {
    var out = CGEventFlags()
    for raw in modifiers {
        switch raw.lowercased() {
        case "cmd", "command", "meta": out.insert(.maskCommand)
        case "win", "windows", "super": out.insert(.maskCommand)
        case "ctrl", "control": out.insert(.maskControl)
        case "alt", "option": out.insert(.maskAlternate)
        case "shift": out.insert(.maskShift)
        default: break
        }
    }
    return out
}

func postKey(_ keyCode: CGKeyCode, modifiers: [String] = []) {
    let source = CGEventSource(stateID: .hidSystemState)
    let eventFlags = flags(modifiers)
    let down = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true)
    down?.flags = eventFlags
    down?.post(tap: .cghidEventTap)
    usleep(15000)
    let up = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false)
    up?.flags = eventFlags
    up?.post(tap: .cghidEventTap)
    usleep(15000)
}

func postText(_ text: String) {
    let source = CGEventSource(stateID: .hidSystemState)
    for character in text {
        let units = Array(String(character).utf16)
        units.withUnsafeBufferPointer { buffer in
            guard let base = buffer.baseAddress else { return }
            let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true)
            down?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: base)
            down?.post(tap: .cghidEventTap)
            usleep(8000)
            let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
            up?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: base)
            up?.post(tap: .cghidEventTap)
            usleep(8000)
        }
    }
}

let args = CommandLine.arguments
if args.count < 2 {
    FileHandle.standardError.write(Data("usage: macos_keys type TEXT | press KEY [modifiers...]\n".utf8))
    exit(64)
}

let mode = args[1]
if mode == "type" {
    if args.count < 3 {
        FileHandle.standardError.write(Data("type requires text\n".utf8))
        exit(64)
    }
    postText(args[2])
} else if mode == "press" {
    if args.count < 3 {
        FileHandle.standardError.write(Data("press requires key\n".utf8))
        exit(64)
    }
    let key = args[2].lowercased()
    guard let code = keyCodes[key] else {
        FileHandle.standardError.write(Data("unsupported key: \(key)\n".utf8))
        exit(64)
    }
    postKey(code, modifiers: Array(args.dropFirst(3)))
} else {
    FileHandle.standardError.write(Data("unknown mode: \(mode)\n".utf8))
    exit(64)
}
'''

_ACTIVATE_HELPER_SOURCE = r'''
import AppKit
import ApplicationServices
import Foundation

func emit(_ payload: [String: Any], code: Int32) -> Never {
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        FileHandle.standardError.write(Data("failed to encode activation result: \(error)\n".utf8))
    }
    exit(code)
}

func clean(_ value: String?) -> String {
    return (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
}

let args = CommandLine.arguments
let pidText = args.count > 1 ? clean(args[1]) : ""
let requestedName = args.count > 2 ? clean(args[2]) : ""
let requestedBundleId = args.count > 3 ? clean(args[3]) : ""
let requestedPath = args.count > 4 ? clean(args[4]) : ""

let requestedPid = Int32(pidText)
let workspace = NSWorkspace.shared
let running = workspace.runningApplications.filter { !$0.isTerminated }

func appName(_ app: NSRunningApplication) -> String {
    if let name = app.localizedName, !name.isEmpty {
        return name
    }
    if let bundleName = app.bundleURL?.deletingPathExtension().lastPathComponent, !bundleName.isEmpty {
        return bundleName
    }
    if let executableName = app.executableURL?.lastPathComponent, !executableName.isEmpty {
        return executableName
    }
    return String(app.processIdentifier)
}

func nameHaystack(_ app: NSRunningApplication) -> String {
    return [
        app.localizedName ?? "",
        app.bundleIdentifier ?? "",
        app.bundleURL?.lastPathComponent ?? "",
        app.bundleURL?.deletingPathExtension().lastPathComponent ?? "",
        app.executableURL?.lastPathComponent ?? "",
    ].joined(separator: " ").lowercased()
}

func exactName(_ app: NSRunningApplication, _ needle: String) -> Bool {
    let lowerNeedle = needle.lowercased()
    let values = [
        app.localizedName ?? "",
        app.bundleURL?.deletingPathExtension().lastPathComponent ?? "",
        app.executableURL?.deletingPathExtension().lastPathComponent ?? "",
    ].map { $0.lowercased() }
    return values.contains(lowerNeedle)
}

var target: NSRunningApplication? = nil
if let pid = requestedPid, pid > 0 {
    target = NSRunningApplication(processIdentifier: pid_t(pid))
}
if target == nil && !requestedBundleId.isEmpty {
    target = NSRunningApplication.runningApplications(withBundleIdentifier: requestedBundleId).first { !$0.isTerminated }
}
if target == nil && !requestedPath.isEmpty {
    let lowerPath = requestedPath.lowercased()
    target = running.first { app in
        (app.bundleURL?.path.lowercased() == lowerPath) || (app.executableURL?.path.lowercased() == lowerPath)
    }
}
if target == nil && !requestedName.isEmpty {
    let exactMatches = running.filter { exactName($0, requestedName) }
    target = exactMatches.first ?? running.first { nameHaystack($0).contains(requestedName.lowercased()) }
}

guard let app = target else {
    let active = workspace.frontmostApplication
    emit([
        "foreground": false,
        "targetFound": false,
        "requestedProcessId": requestedPid.map { Int($0) } as Any,
        "requestedName": requestedName,
        "requestedBundleId": requestedBundleId,
        "requestedPath": requestedPath,
        "activeProcessId": active.map { Int($0.processIdentifier) } as Any,
        "activeProcessName": active.map { appName($0) } as Any,
        "error": "window not found",
    ], code: 1)
}

let axTrusted = AXIsProcessTrusted()
var axFrontmostSet = false
var axFrontmostError = ""
var axRaisedWindow = false
var axRaiseError = ""
var axWindowCount = 0

if axTrusted {
    let appElement = AXUIElementCreateApplication(app.processIdentifier)
    let setError = AXUIElementSetAttributeValue(appElement, kAXFrontmostAttribute as CFString, kCFBooleanTrue)
    axFrontmostSet = setError == .success
    axFrontmostError = String(describing: setError)

    var windowsRef: CFTypeRef?
    let windowsError = AXUIElementCopyAttributeValue(appElement, kAXWindowsAttribute as CFString, &windowsRef)
    if windowsError == .success, let windows = windowsRef as? [AXUIElement] {
        axWindowCount = windows.count
        if let firstWindow = windows.first {
            let raiseError = AXUIElementPerformAction(firstWindow, kAXRaiseAction as CFString)
            axRaisedWindow = raiseError == .success
            axRaiseError = String(describing: raiseError)
        }
    } else {
        axRaiseError = String(describing: windowsError)
    }
}

let nsActivated = app.activate(options: [.activateAllWindows])
let deadline = Date().addingTimeInterval(1.5)
var active = workspace.frontmostApplication
var foreground = (active?.processIdentifier == app.processIdentifier) || app.isActive
while !foreground && Date() < deadline {
    usleep(100_000)
    active = workspace.frontmostApplication
    foreground = (active?.processIdentifier == app.processIdentifier) || app.isActive
}

let payload: [String: Any] = [
    "foreground": foreground,
    "targetFound": true,
    "processId": Int(app.processIdentifier),
    "processName": appName(app),
    "bundleId": app.bundleIdentifier as Any,
    "path": app.bundleURL?.path as Any,
    "activeProcessId": active.map { Int($0.processIdentifier) } as Any,
    "activeProcessName": active.map { appName($0) } as Any,
    "axTrusted": axTrusted,
    "axFrontmostSet": axFrontmostSet,
    "axFrontmostError": axFrontmostError,
    "axRaisedWindow": axRaisedWindow,
    "axRaiseError": axRaiseError,
    "axWindowCount": axWindowCount,
    "nsActivated": nsActivated,
    "error": foreground ? "" : "window did not become foreground",
]
emit(payload, code: foreground ? 0 : 1)
'''

_APPS_HELPER_SOURCE = r'''
import AppKit
import Foundation

func emit(_ payload: Any, code: Int32) -> Never {
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        FileHandle.standardError.write(Data("failed to encode app list: \(error)\n".utf8))
    }
    exit(code)
}

func appName(_ app: NSRunningApplication) -> String {
    if let name = app.localizedName, !name.isEmpty {
        return name
    }
    if let bundleName = app.bundleURL?.deletingPathExtension().lastPathComponent, !bundleName.isEmpty {
        return bundleName
    }
    if let executableName = app.executableURL?.lastPathComponent, !executableName.isEmpty {
        return executableName
    }
    return String(app.processIdentifier)
}

let workspace = NSWorkspace.shared
let activePid = workspace.frontmostApplication?.processIdentifier
let rows = workspace.runningApplications
    .filter { !$0.isTerminated && $0.activationPolicy == .regular }
    .map { app -> [String: Any] in
        return [
            "name": appName(app),
            "processId": Int(app.processIdentifier),
            "bundleId": app.bundleIdentifier as Any,
            "path": app.bundleURL?.path as Any,
            "frontmost": activePid == app.processIdentifier || app.isActive,
        ]
    }
emit(rows, code: 0)
'''

_SNAPSHOT_HELPER_SOURCE = r'''
import AppKit
import ApplicationServices
import Foundation

func emit(_ payload: [String: Any], code: Int32) -> Never {
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        FileHandle.standardError.write(Data("failed to encode snapshot: \(error)\n".utf8))
    }
    exit(code)
}

func clean(_ value: String?) -> String {
    return (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
}

func appName(_ app: NSRunningApplication) -> String {
    if let name = app.localizedName, !name.isEmpty {
        return name
    }
    if let bundleName = app.bundleURL?.deletingPathExtension().lastPathComponent, !bundleName.isEmpty {
        return bundleName
    }
    if let executableName = app.executableURL?.lastPathComponent, !executableName.isEmpty {
        return executableName
    }
    return String(app.processIdentifier)
}

func nameHaystack(_ app: NSRunningApplication) -> String {
    return [
        app.localizedName ?? "",
        app.bundleIdentifier ?? "",
        app.bundleURL?.lastPathComponent ?? "",
        app.bundleURL?.deletingPathExtension().lastPathComponent ?? "",
        app.executableURL?.lastPathComponent ?? "",
    ].joined(separator: " ").lowercased()
}

func exactName(_ app: NSRunningApplication, _ needle: String) -> Bool {
    let lowerNeedle = needle.lowercased()
    let values = [
        app.localizedName ?? "",
        app.bundleURL?.deletingPathExtension().lastPathComponent ?? "",
        app.executableURL?.deletingPathExtension().lastPathComponent ?? "",
    ].map { $0.lowercased() }
    return values.contains(lowerNeedle)
}

func stringAttribute(_ element: AXUIElement, _ attribute: CFString) -> String {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute, &value)
    if error != .success {
        return ""
    }
    if let text = value as? String {
        return text
    }
    if let number = value as? NSNumber {
        return number.stringValue
    }
    return value.map { String(describing: $0) } ?? ""
}

func boolAttribute(_ element: AXUIElement, _ attribute: CFString) -> Bool? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute, &value)
    if error != .success {
        return nil
    }
    if let boolValue = value as? Bool {
        return boolValue
    }
    if let number = value as? NSNumber {
        return number.boolValue
    }
    return nil
}

func pointAttribute(_ element: AXUIElement) -> CGPoint? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &value)
    if error != .success, value == nil {
        return nil
    }
    guard let rawValue = value, CFGetTypeID(rawValue) == AXValueGetTypeID() else {
        return nil
    }
    let axValue = rawValue as! AXValue
    var point = CGPoint.zero
    if AXValueGetValue(axValue, .cgPoint, &point) {
        return point
    }
    return nil
}

func sizeAttribute(_ element: AXUIElement) -> CGSize? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &value)
    if error != .success, value == nil {
        return nil
    }
    guard let rawValue = value, CFGetTypeID(rawValue) == AXValueGetTypeID() else {
        return nil
    }
    let axValue = rawValue as! AXValue
    var size = CGSize.zero
    if AXValueGetValue(axValue, .cgSize, &size) {
        return size
    }
    return nil
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value)
    if error != .success {
        return []
    }
    return (value as? [AXUIElement]) ?? []
}

func actionNames(_ element: AXUIElement) -> [String] {
    var value: CFArray?
    let error = AXUIElementCopyActionNames(element, &value)
    if error != .success {
        return []
    }
    return ((value as? [String]) ?? []).sorted()
}

func settableAttributes(_ element: AXUIElement) -> [String] {
    let candidates: [CFString] = [
        kAXValueAttribute as CFString,
        kAXFocusedAttribute as CFString,
        kAXSelectedAttribute as CFString,
    ]
    var result: [String] = []
    for attribute in candidates {
        var isSettable = DarwinBoolean(false)
        let error = AXUIElementIsAttributeSettable(element, attribute, &isSettable)
        if error == .success && isSettable.boolValue {
            result.append(attribute as String)
        }
    }
    return result.sorted()
}

func windows(_ element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXWindowsAttribute as CFString, &value)
    if error != .success {
        return []
    }
    return (value as? [AXUIElement]) ?? []
}

let args = CommandLine.arguments
let pidText = args.count > 1 ? clean(args[1]) : ""
let requestedName = args.count > 2 ? clean(args[2]) : ""
let maxElements = max(1, min(Int(args.count > 3 ? args[3] : "") ?? 120, 500))
let maxDepth = max(0, min(Int(args.count > 4 ? args[4] : "") ?? 4, 20))
let requestedPid = Int32(pidText)
let workspace = NSWorkspace.shared
let running = workspace.runningApplications.filter { !$0.isTerminated }

var target: NSRunningApplication? = nil
if let pid = requestedPid, pid > 0 {
    target = NSRunningApplication(processIdentifier: pid_t(pid))
}
if target == nil && !requestedName.isEmpty {
    let exactMatches = running.filter { exactName($0, requestedName) }
    target = exactMatches.first ?? running.first { nameHaystack($0).contains(requestedName.lowercased()) }
}
if target == nil && pidText.isEmpty && requestedName.isEmpty {
    target = workspace.frontmostApplication
}

guard let app = target else {
    emit([
        "ok": false,
        "error": "target application process not found",
        "elements": [],
    ], code: 1)
}

if !AXIsProcessTrusted() {
    emit([
        "ok": false,
        "appName": appName(app),
        "processId": Int(app.processIdentifier),
        "error": "macOS Accessibility permission is disabled",
        "elements": [],
    ], code: 1)
}

let appElement = AXUIElementCreateApplication(app.processIdentifier)
let appWindows = windows(appElement)
var rootElement = appElement
var rootPath = "p1"
var title = ""
var windowPayload: [String: Any]? = nil
if let firstWindow = appWindows.first {
    rootElement = firstWindow
    rootPath = "w1"
    title = stringAttribute(firstWindow, kAXTitleAttribute as CFString)
    if let point = pointAttribute(firstWindow), let size = sizeAttribute(firstWindow) {
        windowPayload = [
            "x": Int(point.x.rounded()),
            "y": Int(point.y.rounded()),
            "width": Int(size.width.rounded()),
            "height": Int(size.height.rounded()),
        ]
    }
}

var rows: [[String: Any]] = []
func appendElement(_ element: AXUIElement, path: String, depth: Int) {
    if rows.count >= maxElements {
        return
    }
    let childRows = children(element)
    var row: [String: Any] = [
        "path": path,
        "role": stringAttribute(element, kAXRoleAttribute as CFString),
        "subrole": stringAttribute(element, kAXSubroleAttribute as CFString),
        "name": stringAttribute(element, kAXTitleAttribute as CFString),
        "description": stringAttribute(element, kAXDescriptionAttribute as CFString),
        "value": stringAttribute(element, kAXValueAttribute as CFString),
        "axActions": actionNames(element),
        "settableAttributes": settableAttributes(element),
        "children": childRows.count,
    ]
    if let enabled = boolAttribute(element, kAXEnabledAttribute as CFString) {
        row["enabled"] = enabled
    }
    if let point = pointAttribute(element) {
        row["x"] = Int(point.x.rounded())
        row["y"] = Int(point.y.rounded())
    }
    if let size = sizeAttribute(element) {
        row["width"] = Int(size.width.rounded())
        row["height"] = Int(size.height.rounded())
    }
    rows.append(row)
    if depth >= maxDepth {
        return
    }
    for (index, child) in childRows.enumerated() {
        if rows.count >= maxElements {
            break
        }
        appendElement(child, path: "\(path).\(index + 1)", depth: depth + 1)
    }
}

appendElement(rootElement, path: rootPath, depth: 0)

var payload: [String: Any] = [
    "ok": true,
    "appName": appName(app),
    "processId": Int(app.processIdentifier),
    "title": title,
    "elements": rows,
    "windowCount": appWindows.count,
]
if let windowPayload {
    payload["window"] = windowPayload
}
emit(payload, code: 0)
'''

_AX_ACTION_HELPER_SOURCE = r'''
import AppKit
import ApplicationServices
import Foundation

func emit(_ payload: [String: Any], code: Int32) -> Never {
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        FileHandle.standardError.write(Data("failed to encode action: \(error)\n".utf8))
    }
    exit(code)
}

func fail(_ message: String, code: Int32 = 1, extra: [String: Any] = [:]) -> Never {
    var payload: [String: Any] = [
        "ok": false,
        "error": message,
    ]
    for (key, value) in extra {
        payload[key] = value
    }
    emit(payload, code: code)
}

func clean(_ value: String?) -> String {
    let text = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    if text == "missing value" || text == "button" || text == "text entry area" {
        return ""
    }
    return text
}

func appName(_ app: NSRunningApplication) -> String {
    if let name = app.localizedName, !name.isEmpty {
        return name
    }
    if let bundleName = app.bundleURL?.deletingPathExtension().lastPathComponent, !bundleName.isEmpty {
        return bundleName
    }
    if let executableName = app.executableURL?.lastPathComponent, !executableName.isEmpty {
        return executableName
    }
    return String(app.processIdentifier)
}

func nameHaystack(_ app: NSRunningApplication) -> String {
    return [
        app.localizedName ?? "",
        app.bundleIdentifier ?? "",
        app.bundleURL?.lastPathComponent ?? "",
        app.bundleURL?.deletingPathExtension().lastPathComponent ?? "",
        app.executableURL?.lastPathComponent ?? "",
    ].joined(separator: " ").lowercased()
}

func exactName(_ app: NSRunningApplication, _ needle: String) -> Bool {
    let lowerNeedle = needle.lowercased()
    let values = [
        app.localizedName ?? "",
        app.bundleURL?.deletingPathExtension().lastPathComponent ?? "",
        app.executableURL?.deletingPathExtension().lastPathComponent ?? "",
    ].map { $0.lowercased() }
    return values.contains(lowerNeedle)
}

func stringAttribute(_ element: AXUIElement, _ attribute: CFString) -> String {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute, &value)
    if error != .success {
        return ""
    }
    if let text = value as? String {
        return text
    }
    if let number = value as? NSNumber {
        return number.stringValue
    }
    return value.map { String(describing: $0) } ?? ""
}

func identityName(_ element: AXUIElement) -> String {
    for attribute in [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute] {
        let text = clean(stringAttribute(element, attribute as CFString))
        if !text.isEmpty {
            return text
        }
    }
    return ""
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value)
    if error != .success {
        return []
    }
    return (value as? [AXUIElement]) ?? []
}

func windows(_ element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXWindowsAttribute as CFString, &value)
    if error != .success {
        return []
    }
    return (value as? [AXUIElement]) ?? []
}

func parsedPath(_ path: String) -> (rootKind: Character, rootIndex: Int, indexes: [Int])? {
    let parts = path.split(separator: ".").map(String.init)
    guard let root = parts.first, root.count >= 2 else {
        return nil
    }
    guard let rootKind = root.first, rootKind == "w" || rootKind == "p" else {
        return nil
    }
    guard let rootIndex = Int(String(root.dropFirst())), rootIndex > 0 else {
        return nil
    }
    var indexes: [Int] = []
    for part in parts.dropFirst() {
        guard let index = Int(part), index > 0 else {
            return nil
        }
        indexes.append(index)
    }
    return (rootKind, rootIndex, indexes)
}

func scrollAction(_ payload: String) -> (nativeAction: String, direction: String, unit: String, amount: Int)? {
    let parts = payload.split(separator: ":").map { String($0).lowercased() }
    let direction = parts.count > 0 && !parts[0].isEmpty ? parts[0] : "down"
    let unit = parts.count > 1 && !parts[1].isEmpty ? parts[1] : "page"
    let rawAmount = parts.count > 2 ? Int(parts[2]) ?? 1 : 1
    let amount = max(1, min(rawAmount, unit == "line" ? 40 : 10))
    let directionSuffix: String
    switch direction {
    case "down": directionSuffix = "Down"
    case "up": directionSuffix = "Up"
    case "left": directionSuffix = "Left"
    case "right": directionSuffix = "Right"
    default: return nil
    }
    let unitSuffix: String
    switch unit {
    case "page": unitSuffix = "Page"
    case "line": unitSuffix = "Line"
    default: return nil
    }
    return ("AXScroll\(directionSuffix)By\(unitSuffix)", direction, unit, amount)
}

func numberAttribute(_ element: AXUIElement, _ attribute: CFString) -> Double? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute, &value)
    if error != .success {
        return nil
    }
    if let number = value as? NSNumber {
        return number.doubleValue
    }
    if let text = value as? String {
        return Double(text.trimmingCharacters(in: .whitespacesAndNewlines))
    }
    return nil
}

func sizeAttribute(_ element: AXUIElement) -> CGSize? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &value)
    if error != .success {
        return nil
    }
    guard let value else {
        return nil
    }
    guard CFGetTypeID(value) == AXValueGetTypeID() else {
        return nil
    }
    let axValue = value as! AXValue
    var size = CGSize.zero
    if AXValueGetValue(axValue, AXValueType.cgSize, &size) {
        return size
    }
    return nil
}

func isVerticalScrollBar(_ element: AXUIElement) -> Bool {
    let orientation = stringAttribute(element, "AXOrientation" as CFString).lowercased()
    if orientation.contains("vertical") {
        return true
    }
    if orientation.contains("horizontal") {
        return false
    }
    if let size = sizeAttribute(element) {
        return size.height >= size.width
    }
    return true
}

func scrollBarDescendants(_ element: AXUIElement, depth: Int = 0) -> [AXUIElement] {
    if depth > 4 {
        return []
    }
    var rows: [AXUIElement] = []
    for child in children(element) {
        if stringAttribute(child, kAXRoleAttribute as CFString) == "AXScrollBar" {
            rows.append(child)
        }
        rows.append(contentsOf: scrollBarDescendants(child, depth: depth + 1))
    }
    return rows
}

func setScrollBarValue(_ targetElement: AXUIElement, _ scroll: (nativeAction: String, direction: String, unit: String, amount: Int)) -> [String: Any]? {
    let wantsVertical = scroll.direction == "up" || scroll.direction == "down"
    let bars = scrollBarDescendants(targetElement)
    let candidates = bars.filter { wantsVertical == isVerticalScrollBar($0) }
    guard let bar = candidates.first ?? bars.first else {
        return nil
    }
    let minValue = numberAttribute(bar, "AXMinValue" as CFString) ?? 0.0
    let maxValue = numberAttribute(bar, "AXMaxValue" as CFString) ?? 1.0
    let currentValue = numberAttribute(bar, kAXValueAttribute as CFString) ?? minValue
    let range = max(0.0001, maxValue - minValue)
    let step = (scroll.unit == "line" ? 0.06 : 0.35) * range * Double(scroll.amount)
    let sign = (scroll.direction == "up" || scroll.direction == "left") ? -1.0 : 1.0
    let nextValue = min(max(currentValue + (sign * step), minValue), maxValue)
    if abs(nextValue - currentValue) < 0.000001 {
        return [
            "ok": false,
            "error": "scrollbar is already at boundary",
            "currentValue": currentValue,
            "nextValue": nextValue,
        ]
    }
    let error = AXUIElementSetAttributeValue(bar, kAXValueAttribute as CFString, NSNumber(value: nextValue))
    if error != .success {
        return [
            "ok": false,
            "error": "AXValue scrollbar set failed: \(error.rawValue)",
            "currentValue": currentValue,
            "nextValue": nextValue,
        ]
    }
    return [
        "ok": true,
        "nativeAction": "setScrollBarValue",
        "requestedNativeAction": scroll.nativeAction,
        "currentValue": currentValue,
        "nextValue": nextValue,
    ]
}

let args = CommandLine.arguments
let pidText = args.count > 1 ? clean(args[1]) : ""
let requestedName = args.count > 2 ? clean(args[2]) : ""
let targetPath = args.count > 3 ? clean(args[3]) : ""
let actionName = args.count > 4 ? clean(args[4]).lowercased() : "click"
let textValue = args.count > 5 ? (args[5]) : ""
let expectedRole = args.count > 6 ? clean(args[6]) : ""
let expectedName = args.count > 7 ? clean(args[7]) : ""
let requestedPid = Int32(pidText)
let workspace = NSWorkspace.shared
let running = workspace.runningApplications.filter { !$0.isTerminated }

guard let path = parsedPath(targetPath) else {
    fail("unsupported Accessibility path: \(targetPath)", extra: ["path": targetPath, "action": actionName])
}

var target: NSRunningApplication? = nil
if let pid = requestedPid, pid > 0 {
    target = NSRunningApplication(processIdentifier: pid_t(pid))
}
if target == nil && !requestedName.isEmpty {
    let exactMatches = running.filter { exactName($0, requestedName) }
    target = exactMatches.first ?? running.first { nameHaystack($0).contains(requestedName.lowercased()) }
}
if target == nil && pidText.isEmpty && requestedName.isEmpty {
    target = workspace.frontmostApplication
}

guard let app = target else {
    fail("target application process not found", extra: ["path": targetPath, "action": actionName])
}

if !AXIsProcessTrusted() {
    fail("macOS Accessibility permission is disabled", extra: [
        "appName": appName(app),
        "processId": Int(app.processIdentifier),
        "path": targetPath,
        "action": actionName,
    ])
}

let appElement = AXUIElementCreateApplication(app.processIdentifier)
let rootElement: AXUIElement
if path.rootKind == "w" {
    let appWindows = windows(appElement)
    let windowIndex = path.rootIndex - 1
    guard windowIndex >= 0 && windowIndex < appWindows.count else {
        fail("Accessibility window path segment not found: \(targetPath)", extra: ["path": targetPath, "action": actionName])
    }
    rootElement = appWindows[windowIndex]
} else {
    rootElement = appElement
}

var targetElement = rootElement
for index in path.indexes {
    let childRows = children(targetElement)
    let childIndex = index - 1
    guard childIndex >= 0 && childIndex < childRows.count else {
        fail("Accessibility path segment not found: \(targetPath)", extra: ["path": targetPath, "action": actionName])
    }
    targetElement = childRows[childIndex]
}

let currentRole = stringAttribute(targetElement, kAXRoleAttribute as CFString)
let currentName = identityName(targetElement)
if !expectedRole.isEmpty && currentRole != expectedRole {
    fail("desktop.act ref role changed from \(expectedRole) to \(currentRole); call desktop.snapshot again", extra: [
        "identityMismatch": true,
        "path": targetPath,
        "action": actionName,
        "expectedRole": expectedRole,
        "expectedName": expectedName,
        "currentRole": currentRole,
        "currentName": currentName,
    ])
}
if !expectedName.isEmpty && currentName != expectedName {
    fail("desktop.act ref name changed from \(expectedName) to \(currentName); call desktop.snapshot again", extra: [
        "identityMismatch": true,
        "path": targetPath,
        "action": actionName,
        "expectedRole": expectedRole,
        "expectedName": expectedName,
        "currentRole": currentRole,
        "currentName": currentName,
    ])
}

if actionName == "click" {
    let error = AXUIElementPerformAction(targetElement, kAXPressAction as CFString)
    if error != .success {
        fail("AXPress failed: \(error.rawValue)", extra: ["path": targetPath, "action": actionName, "currentRole": currentRole, "currentName": currentName])
    }
    emit([
        "ok": true,
        "nativeAction": "AXPress",
        "inputMethod": "accessibility",
        "path": targetPath,
        "action": actionName,
        "name": currentName,
        "role": currentRole,
    ], code: 0)
} else if actionName == "type" || actionName == "paste" {
    let error = AXUIElementSetAttributeValue(targetElement, kAXValueAttribute as CFString, textValue as CFString)
    if error != .success {
        fail("AXValue set failed: \(error.rawValue)", extra: ["path": targetPath, "action": actionName, "currentRole": currentRole, "currentName": currentName])
    }
    emit([
        "ok": true,
        "nativeAction": "setValue",
        "inputMethod": "accessibility",
        "path": targetPath,
        "action": actionName,
        "name": currentName,
        "role": currentRole,
    ], code: 0)
} else if actionName == "scroll" {
    guard let scroll = scrollAction(textValue) else {
        fail("unsupported scroll request", extra: ["path": targetPath, "action": actionName, "scrollPayload": textValue])
    }
    _ = AXUIElementSetAttributeValue(targetElement, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    var performed = 0
    var performError: AXError? = nil
    for _ in 0..<scroll.amount {
        let error = AXUIElementPerformAction(targetElement, scroll.nativeAction as CFString)
        if error != .success {
            performError = error
            break
        }
        performed += 1
    }
    if performed < scroll.amount {
        if let fallback = setScrollBarValue(targetElement, scroll), fallback["ok"] as? Bool == true {
            emit([
                "ok": true,
                "nativeAction": "setScrollBarValue",
                "requestedNativeAction": scroll.nativeAction,
                "inputMethod": "accessibility",
                "path": targetPath,
                "action": actionName,
                "name": currentName,
                "role": currentRole,
                "direction": scroll.direction,
                "unit": scroll.unit,
                "amount": scroll.amount,
                "performed": performed,
                "fallback": fallback,
                "fallbackFromError": performError.map { $0.rawValue } ?? NSNull(),
            ], code: 0)
        }
        var extra: [String: Any] = [
            "path": targetPath,
            "action": actionName,
            "nativeAction": scroll.nativeAction,
            "currentRole": currentRole,
            "currentName": currentName,
            "direction": scroll.direction,
            "unit": scroll.unit,
            "amount": scroll.amount,
            "performed": performed,
        ]
        if let performError {
            extra["performError"] = performError.rawValue
        }
        fail("\(scroll.nativeAction) failed: \(performError.map { String($0.rawValue) } ?? "unknown")", extra: extra)
    }
    emit([
        "ok": true,
        "nativeAction": scroll.nativeAction,
        "inputMethod": "accessibility",
        "path": targetPath,
        "action": actionName,
        "name": currentName,
        "role": currentRole,
        "direction": scroll.direction,
        "unit": scroll.unit,
        "amount": scroll.amount,
        "performed": performed,
    ], code: 0)
}

fail("unsupported native action: \(actionName)", extra: ["path": targetPath, "action": actionName])
'''

_MODIFIER_ALIASES = {
    "cmd": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "ctrl": "control",
    "control": "control",
    "alt": "option",
    "option": "option",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "super": "win",
}
_KEY_ALIASES = {
    "esc": "escape",
    "enter": "return",
    "page down": "pagedown",
    "page_down": "pagedown",
    "page up": "pageup",
    "page_up": "pageup",
    "backspace": "delete",
    "forward_delete": "forwarddelete",
    "del": "forwarddelete",
    "ins": "insert",
}


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None, None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _powershell_executable(*, sta: bool = False) -> str | None:
    if sta and _is_windows():
        for name in ("powershell.exe", "powershell"):
            resolved = shutil.which(name)
            if resolved:
                return resolved
        for candidate in _WINDOWS_POWERSHELL_CANDIDATES[:2]:
            if Path(candidate).exists():
                return candidate
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    if _is_windows():
        for candidate in _WINDOWS_POWERSHELL_CANDIDATES:
            if Path(candidate).exists():
                return candidate
    return None


def _powershell_command(script: str, *, sta: bool = False) -> list[str] | None:
    executable = _powershell_executable(sta=sta)
    if not executable:
        return None
    if _is_windows():
        script = "\n".join([
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "$OutputEncoding = [Console]::OutputEncoding",
            script,
        ])
    base = [executable, "-NoProfile", "-NonInteractive"]
    executable_name = Path(executable).name.lower()
    if _is_windows() and sta and executable_name in {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}:
        base.append("-STA")
    if executable_name.startswith("powershell"):
        base.extend(["-ExecutionPolicy", "Bypass"])
    return [*base, "-Command", script]


def _ps_string(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _windows_argument_list(values: list[Any]) -> str:
    return subprocess.list2cmdline([str(value) for value in values])


def _run_windows_powershell(
    script: str,
    run_process: Callable[..., dict[str, Any]],
    *,
    timeout: float = 15.0,
    sta: bool = False,
) -> dict[str, Any]:
    command = _powershell_command(script, sta=sta)
    if command is None:
        return {
            "command": ["powershell"],
            "returnCode": 127,
            "stdout": "",
            "stderr": "PowerShell is required for Windows desktop/browser bridge tools",
            "method": "powershell",
        }
    result = _run_visual_command(run_process, command, timeout=timeout)
    result["method"] = "powershell"
    return result


def _visual_output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_visual_command(
    run_process: Callable[..., dict[str, Any]],
    command: list[str],
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return run_process(command, **kwargs)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if exc.stdout is not None else getattr(exc, "output", None)
        timeout = kwargs.get("timeout")
        return {
            "command": command,
            "returnCode": None,
            "timeout": True,
            "stdout": _visual_output_text(stdout),
            "stderr": _visual_output_text(exc.stderr) or f"command timed out after {timeout}s",
        }
    except Exception as exc:
        return {
            "command": command,
            "returnCode": 1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def _is_default_visual_run_process(run_process: Callable[..., dict[str, Any]]) -> bool:
    return getattr(run_process, "__name__", "") == "_run_process"


def _terminate_process_tree(process: subprocess.Popen[bytes], *, grace_seconds: float = 2.0) -> None:
    if process.poll() is not None:
        return
    if _is_windows():
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                text=False,
                capture_output=True,
                timeout=max(1.0, grace_seconds),
                check=False,
            )
        return
    with contextlib.suppress(Exception):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(Exception):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            process.wait(timeout=grace_seconds)


def _run_browser_playwright_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = 10.0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    popen_kwargs: dict[str, Any] = {}
    if _is_windows():
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return {
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "returnCode": process.returncode,
            "stdout": _visual_output_text(stdout),
            "stderr": _visual_output_text(stderr),
        }
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        timeout_stderr = _visual_output_text(stderr) or _visual_output_text(exc.stderr) or f"command timed out after {timeout}s"
        return {
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "returnCode": None,
            "timeout": True,
            "processTreeTerminated": True,
            "stdout": _visual_output_text(stdout) or _visual_output_text(exc.stdout) or _visual_output_text(getattr(exc, "output", None)),
            "stderr": timeout_stderr,
        }


def _windows_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "windows_visual.py", helper_dir / "windows_visual.sha256"


def _ensure_windows_visual_helper() -> Path:
    source_path, digest_path = _windows_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_WINDOWS_VISUAL_HELPER_SOURCE.encode("utf-8")).hexdigest()
    if source_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return source_path
    source_path.write_text(_WINDOWS_VISUAL_HELPER_SOURCE, encoding="utf-8")
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return source_path


def _run_windows_visual_helper(
    mode: str,
    payload: dict[str, Any],
    run_process: Callable[..., dict[str, Any]],
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    helper = _ensure_windows_visual_helper()
    result = run_process(
        [sys.executable, str(helper), mode, json.dumps(payload, ensure_ascii=False)],
        timeout=timeout,
    )
    result["method"] = "win32_sendinput" if mode in {"click", "keypress", "type", "scroll"} else "win32"
    rows = _json_rows_from_stdout(result)
    if not rows:
        parsed = _json_value_from_stdout(result.get("stdout"))
        if isinstance(parsed, dict):
            rows = [parsed]
        elif isinstance(parsed, list):
            rows = [item for item in parsed if isinstance(item, dict)]
    if rows:
        result["helper"] = rows[0]
        if "ok" in rows[0]:
            result["ok"] = bool(rows[0]["ok"])
        if rows[0].get("mode"):
            result["helperMode"] = rows[0]["mode"]
        if rows[0].get("inputMethod"):
            result["inputMethod"] = rows[0]["inputMethod"]
        if result.get("returnCode") == 0:
            if rows[0].get("ok") is not True:
                result["returnCode"] = 1
                result["stderr"] = str(rows[0].get("error") or rows[0].get("message") or "Windows visual helper reported ok=false")
            elif rows[0].get("mode") != mode:
                result["returnCode"] = 1
                result["stderr"] = f"Windows visual helper returned mode {rows[0].get('mode')!r}; expected {mode!r}"
    elif result.get("returnCode") == 0:
        result["returnCode"] = 1
        result["stderr"] = "Windows visual helper did not return verification metadata"
    return result


def execute_windows_visual_selftest(run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    if not _is_windows():
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": "Windows visual helper selftest is only available on win32",
            "method": "win32",
            "platform": sys.platform,
        }
    result = _run_windows_visual_helper("selftest", {}, run_process, timeout=5.0)
    helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
    result.update({
        "screenWidth": helper.get("screenWidth"),
        "screenHeight": helper.get("screenHeight"),
        "virtualLeft": helper.get("virtualLeft"),
        "virtualTop": helper.get("virtualTop"),
        "virtualWidth": helper.get("virtualWidth"),
        "virtualHeight": helper.get("virtualHeight"),
        "dpiAwareness": helper.get("dpiAwareness"),
        "platform": sys.platform,
    })
    return result


def _windows_dpi_awareness_script_lines() -> list[str]:
    return [
        "$atriumDpiAwareness = $false",
        "try {",
        "  $dpiSig = '[DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware(); [DllImport(\"user32.dll\")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr dpiContext);'",
        "  Add-Type -MemberDefinition $dpiSig -Name Win32Dpi -Namespace ATRIUM -ErrorAction SilentlyContinue",
        "  try { if ([ATRIUM.Win32Dpi]::SetProcessDpiAwarenessContext([IntPtr](-4))) { $atriumDpiAwareness = $true } } catch {}",
        "  if (-not $atriumDpiAwareness) { try { if ([ATRIUM.Win32Dpi]::SetProcessDPIAware()) { $atriumDpiAwareness = $true } } catch {} }",
        "} catch {}",
    ]


def execute_windows_powershell_visual_preflight(run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    if not _is_windows():
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": "Windows PowerShell visual preflight is only available on win32",
            "method": "powershell",
            "platform": sys.platform,
        }
    script = "\n".join([
        "$checks = [ordered]@{",
        "  winForms = $false",
        "  drawing = $false",
        "  virtualScreen = $false",
        "  systemIcon = $false",
        "  setClipboardCommand = $false",
        "  getClipboardCommand = $false",
        "  dpiAwareness = $false",
        "}",
        "$errors = [ordered]@{}",
        "$virtualScreen = [ordered]@{}",
        *_windows_dpi_awareness_script_lines(),
        "$checks.dpiAwareness = [bool]$atriumDpiAwareness",
        "try {",
        "  Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop",
        "  $checks.winForms = $true",
        "  $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen",
        "  $virtualScreen.left = [int]$bounds.Left",
        "  $virtualScreen.top = [int]$bounds.Top",
        "  $virtualScreen.width = [int]$bounds.Width",
        "  $virtualScreen.height = [int]$bounds.Height",
        "  $checks.virtualScreen = ($bounds.Width -gt 0 -and $bounds.Height -gt 0)",
        "} catch {",
        "  $errors.winForms = $_.Exception.Message",
        "}",
        "try {",
        "  Add-Type -AssemblyName System.Drawing -ErrorAction Stop",
        "  $checks.drawing = $true",
        "  $checks.systemIcon = ($null -ne [System.Drawing.SystemIcons]::Information)",
        "} catch {",
        "  $errors.drawing = $_.Exception.Message",
        "}",
        "$checks.setClipboardCommand = ($null -ne (Get-Command Set-Clipboard -ErrorAction SilentlyContinue))",
        "$checks.getClipboardCommand = ($null -ne (Get-Command Get-Clipboard -ErrorAction SilentlyContinue))",
        "$ok = $true",
        "foreach ($name in @('winForms','drawing','virtualScreen','systemIcon','setClipboardCommand','getClipboardCommand','dpiAwareness')) {",
        "  if (-not [bool]$checks[$name]) { $ok = $false }",
        "}",
        "[PSCustomObject]@{ ok = $ok; checks = $checks; virtualScreen = $virtualScreen; errors = $errors; powerShell = $PSVersionTable.PSVersion.ToString() } | ConvertTo-Json -Compress -Depth 5",
    ])
    result = _run_windows_powershell(script, run_process, timeout=8.0, sta=True)
    rows = _json_rows_from_stdout(result)
    row = rows[0] if rows else {}
    result.update({
        "ok": bool(row.get("ok")),
        "checks": row.get("checks") if isinstance(row.get("checks"), dict) else {},
        "virtualScreen": row.get("virtualScreen") if isinstance(row.get("virtualScreen"), dict) else {},
        "errors": row.get("errors") if isinstance(row.get("errors"), dict) else {},
        "powerShell": row.get("powerShell"),
        "platform": sys.platform,
    })
    return result


def execute_screenshot_capture(path: Path, run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_windows():
        script = "\n".join([
            *_windows_dpi_awareness_script_lines(),
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen",
            "$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height",
            "$graphics = [System.Drawing.Graphics]::FromImage($bmp)",
            "$graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)",
            f"$bmp.Save({_ps_string(str(path))}, [System.Drawing.Imaging.ImageFormat]::Png)",
            "$graphics.Dispose()",
            "$bmp.Dispose()",
            f"[PSCustomObject]@{{ path={_ps_string(str(path))}; left=[int]$bounds.Left; top=[int]$bounds.Top; width=[int]$bounds.Width; height=[int]$bounds.Height; dpiAwareness=[bool]$atriumDpiAwareness }} | ConvertTo-Json -Compress",
        ])
        result = _run_windows_powershell(script, run_process, timeout=15.0, sta=True)
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        file_verified = False
        file_bytes = None
        if result.get("returnCode") == 0:
            if not path.is_file():
                result["returnCode"] = 1
                result["stderr"] = "screenshot file was not created"
            else:
                data = path.read_bytes()
                file_bytes = len(data)
                width, height = _png_dimensions(data)
                if not width or not height:
                    result["returnCode"] = 1
                    result["stderr"] = "screenshot file is not a valid PNG"
                else:
                    file_verified = True
                    row = {**row, "width": width, "height": height}
        result.update({
            "path": str(path),
            "left": row.get("left"),
            "top": row.get("top"),
            "width": row.get("width"),
            "height": row.get("height"),
            "dpiAwareness": row.get("dpiAwareness"),
            "fileBytes": file_bytes,
            "fileVerified": file_verified,
            "platform": sys.platform,
        })
        return result
    result = run_process(["screencapture", "-x", str(path)], timeout=10.0)
    width = height = None
    if result.get("returnCode") == 0 and path.is_file():
        width, height = _png_dimensions(path.read_bytes())
    result.update({"path": str(path), "width": width, "height": height, "platform": sys.platform})
    return result


def normalize_browser_profile(raw: Any = None) -> str:
    value = str(raw or "").strip()
    lowered = value.lower()
    if lowered in _USER_BROWSER_PROFILE_ALIASES:
        return "user"
    if lowered in _OWN_BROWSER_PROFILE_ALIASES:
        return "atrium"
    if not _BROWSER_PROFILE_RE.match(value):
        raise ValueError("browser profile must be user, atrium, or a safe name using letters, numbers, '_' or '-'")
    return value


def browser_profile_from_args(args: dict[str, Any]) -> str:
    return normalize_browser_profile(args.get("profile") or args.get("browserProfile"))


def _browser_profiles_root() -> Path:
    return (get_settings().data_dir / "browser-profiles").resolve()


def _browser_control_profiles_root() -> Path:
    return (get_settings().data_dir / "browser-control-profiles").resolve()


def browser_profile_data_dir(profile: str) -> Path | None:
    normalized = normalize_browser_profile(profile)
    if normalized == "user":
        return None
    return _browser_profiles_root() / normalized


def browser_control_profile_data_dir(profile: str) -> Path | None:
    normalized = normalize_browser_profile(profile)
    if normalized == "user":
        return None
    return _browser_control_profiles_root() / normalized


def _browser_app_candidate() -> tuple[str, Path] | None:
    if _is_windows():
        for app_name, app_path in _windows_browser_candidates():
            if app_path.exists():
                return app_name, app_path
        return None
    for app_name, app_path in _BROWSER_APP_CANDIDATES:
        if app_path.exists():
            return app_name, app_path
    return None


def browser_profile_descriptor(profile: str) -> dict[str, Any]:
    normalized = normalize_browser_profile(profile)
    data_dir = browser_profile_data_dir(normalized)
    control_data_dir = browser_control_profile_data_dir(normalized)
    return {
        "id": normalized,
        "kind": "user" if normalized == "user" else "isolated",
        "isOwnProfile": normalized == "atrium",
        "isDefaultUserProfile": normalized == "user",
        "userDataDir": None if data_dir is None else str(data_dir),
        "controlDataDir": None if control_data_dir is None else str(control_data_dir),
        "exists": None if data_dir is None else data_dir.exists(),
        "aliases": ["default", "host", "personal"] if normalized == "user" else (["own", "agent", "system", "isolated"] if normalized == "atrium" else []),
    }


def list_browser_profiles() -> dict[str, Any]:
    root = _browser_profiles_root()
    profiles: dict[str, dict[str, Any]] = {
        "user": browser_profile_descriptor("user"),
        "atrium": browser_profile_descriptor("atrium"),
    }
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.is_dir() and _BROWSER_PROFILE_RE.match(child.name) and child.name not in profiles:
                profiles[child.name] = browser_profile_descriptor(child.name)
    app = _browser_app_candidate()
    return {
        "ownProfile": "atrium",
        "defaultProfile": "user",
        "platform": sys.platform,
        "profilesRoot": str(root),
        "browserApp": None if app is None else {"name": app[0], "path": str(app[1])},
        "profiles": list(profiles.values()),
    }


def _browser_playwright_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "browser_playwright.js", helper_dir / "browser_playwright.sha256"


def _ensure_browser_playwright_helper() -> Path:
    source_path, digest_path = _browser_playwright_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_BROWSER_PLAYWRIGHT_HELPER_SOURCE.encode("utf-8")).hexdigest()
    if source_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return source_path
    source_path.write_text(_BROWSER_PLAYWRIGHT_HELPER_SOURCE, encoding="utf-8")
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return source_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ui_root() -> Path:
    return _repo_root() / "ui"


def _node_executable() -> str | None:
    return shutil.which("node") or shutil.which("node.exe")


def _bool_arg(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _browser_viewport_from_args(args: dict[str, Any]) -> dict[str, int] | None:
    raw = args.get("viewport")
    if not isinstance(raw, dict):
        return None
    width = _bounded_int(raw.get("width"), default=1280, minimum=320, maximum=10000)
    height = _bounded_int(raw.get("height"), default=720, minimum=240, maximum=10000)
    return {"width": width, "height": height}


def _browser_control_profile_from_args(args: dict[str, Any]) -> str:
    raw = args.get("profile") or args.get("browserProfile") or "atrium"
    profile = normalize_browser_profile(raw)
    if profile == "user":
        raise ValueError("browser.snapshot/browser.act require an isolated browser profile; use profile='atrium' or a named profile")
    return profile


def _browser_app_executable_path(app_path: Path) -> str | None:
    if _is_windows():
        return str(app_path) if app_path.exists() else None
    if app_path.suffix.lower() == ".app":
        info_path = app_path / "Contents" / "Info.plist"
        executable_name: str | None = None
        try:
            with info_path.open("rb") as f:
                loaded = plistlib.load(f)
            if isinstance(loaded, dict) and loaded.get("CFBundleExecutable"):
                executable_name = str(loaded["CFBundleExecutable"])
        except Exception:
            executable_name = None
        candidates = []
        if executable_name:
            candidates.append(app_path / "Contents" / "MacOS" / executable_name)
        candidates.append(app_path / "Contents" / "MacOS" / app_path.stem)
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None
    return str(app_path) if app_path.exists() else None


def _browser_playwright_payload(args: dict[str, Any], *, mode: str) -> dict[str, Any]:
    if mode not in {"snapshot", "act"}:
        raise ValueError("browser Playwright mode must be snapshot or act")
    profile = _browser_control_profile_from_args(args)
    data_dir = browser_control_profile_data_dir(profile)
    if data_dir is None:
        raise ValueError("browser.snapshot/browser.act require an isolated browser profile")
    data_dir.mkdir(parents=True, exist_ok=True)
    timeout_ms = _bounded_int(args.get("timeoutMs") or args.get("timeout_ms"), default=15_000, minimum=1000, maximum=120_000)
    payload: dict[str, Any] = {
        "mode": mode,
        "profile": profile,
        "userDataDir": str(data_dir),
        "headless": _bool_arg(args.get("headless"), default=False),
        "includeText": _bool_arg(args.get("includeText"), default=True),
        "maxElements": _bounded_int(args.get("maxElements"), default=80, minimum=1, maximum=300),
        "maxTextChars": _bounded_int(args.get("maxTextChars"), default=12_000, minimum=0, maximum=60_000),
        "timeoutMs": timeout_ms,
    }
    ui_root = _ui_root()
    if ui_root.exists():
        payload["requireFrom"] = str(ui_root)
    viewport = _browser_viewport_from_args(args)
    if viewport:
        payload["viewport"] = viewport
    url = args.get("url")
    if isinstance(url, str) and url.strip():
        payload["url"] = url.strip()
    app = _browser_app_candidate()
    if app:
        executable_path = _browser_app_executable_path(app[1])
        if executable_path:
            payload["executablePath"] = executable_path
            payload["browserApp"] = app[0]
            payload["browserAppPath"] = str(app[1])
    if mode == "act":
        action = str(args.get("action") or "click").strip().lower()
        allowed_actions = {"click", "fill", "type", "press", "check", "uncheck", "select", "hover"}
        if action not in allowed_actions:
            raise ValueError(f"unsupported browser.act action: {action}")
        payload["action"] = action
        payload["waitAfterMs"] = _bounded_int(args.get("waitAfterMs"), default=250, minimum=0, maximum=5000)
        payload["allowStaleRef"] = _bool_arg(args.get("allowStaleRef"), default=False)
        payload["maxRefAgeMs"] = _bounded_int(args.get("maxRefAgeMs"), default=_DESKTOP_REF_MAX_AGE_MS, minimum=1000, maximum=3_600_000)
        for key in ("ref", "selector", "text", "value", "key"):
            if key in args and args[key] is not None:
                payload[key] = args[key]
    return payload


def _browser_playwright_env() -> dict[str, str]:
    env = os.environ.copy()
    node_modules = _ui_root() / "node_modules"
    if node_modules.exists():
        existing = env.get("NODE_PATH")
        env["NODE_PATH"] = str(node_modules) if not existing else os.pathsep.join([str(node_modules), existing])
    return env


def _execute_browser_playwright(
    args: dict[str, Any],
    run_process: Callable[..., dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    try:
        payload = _browser_playwright_payload(args, mode=mode)
    except ValueError as exc:
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": str(exc),
            "backend": "playwright",
            "profile": str(args.get("profile") or args.get("browserProfile") or "atrium"),
            "profileKind": "isolated",
            "platform": sys.platform,
        }
    node = _node_executable()
    if not node:
        return {
            "returnCode": 127,
            "stdout": "",
            "stderr": "Node.js is required for browser.snapshot/browser.act Playwright backend",
            "backend": "playwright",
            "profile": payload.get("profile"),
            "profileKind": "isolated",
            "userDataDir": payload.get("userDataDir"),
            "platform": sys.platform,
        }
    helper = _ensure_browser_playwright_helper()
    workdir = _ui_root() if _ui_root().exists() else _repo_root()
    timeout_seconds = max(5.0, min(float(payload.get("timeoutMs", 15_000)) / 1000.0 + 5.0, 130.0))
    command = [node, str(helper), json.dumps(payload, ensure_ascii=False)]
    if _is_default_visual_run_process(run_process):
        result = _run_browser_playwright_process(
            command,
            cwd=workdir,
            timeout=timeout_seconds,
            env=_browser_playwright_env(),
        )
    else:
        result = _run_visual_command(
            run_process,
            command,
            cwd=workdir,
            timeout=timeout_seconds,
            env=_browser_playwright_env(),
        )
    parsed = _json_value_from_stdout(result.get("stdout"))
    result.update({
        "backend": "playwright",
        "helperPath": str(helper),
        "profile": payload.get("profile"),
        "profileKind": "isolated",
        "isOwnProfile": payload.get("profile") == "atrium",
        "userDataDir": payload.get("userDataDir"),
        "platform": sys.platform,
    })
    if payload.get("browserApp"):
        result["browserApp"] = payload.get("browserApp")
        result["browserAppPath"] = payload.get("browserAppPath")
    if isinstance(parsed, dict):
        raw_stderr = str(result.get("stderr") or "")
        result.update(parsed)
        if raw_stderr and not result.get("stderr"):
            result["stderr"] = raw_stderr
        return result
    if result.get("returnCode") == 0:
        result["returnCode"] = 1
        result["stderr"] = "browser Playwright helper did not return JSON metadata"
    return result


def execute_browser_snapshot(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    return _execute_browser_playwright(args, run_process, mode="snapshot")


def execute_browser_act(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    return _execute_browser_playwright(args, run_process, mode="act")


def _windows_user_browser_open_script(url: str) -> str:
    argument_list = _windows_argument_list([url])
    return "\n".join([
        f"$url = {_ps_string(url)}",
        f"$argumentList = {_ps_string(argument_list)}",
        "$progIds = @()",
        "foreach ($scheme in @('https','http')) {",
        "  try {",
        "    $choice = Get-ItemProperty -Path \"HKCU:\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\$scheme\\UserChoice\" -ErrorAction SilentlyContinue",
        "    if ($choice -and $choice.ProgId) { $progIds += [string]$choice.ProgId }",
        "  } catch {}",
        "}",
        "function Get-AtriumDefaultValue([string]$path) {",
        "  try {",
        "    $item = Get-Item -Path $path -ErrorAction Stop",
        "    return [string]$item.GetValue('')",
        "  } catch { return $null }",
        "}",
        "function Resolve-AtriumExecutableFromCommand([string]$commandText) {",
        "  if (-not $commandText) { return $null }",
        "  $trimmed = $commandText.Trim()",
        "  if ($trimmed.StartsWith('\"')) {",
        "    $end = $trimmed.IndexOf('\"', 1)",
        "    if ($end -gt 1) { return $trimmed.Substring(1, $end - 1) }",
        "  }",
        "  $match = [regex]::Match($trimmed, '^[^\\s]+\\.exe')",
        "  if ($match.Success) { return $match.Value }",
        "  return $null",
        "}",
        "$browserPath = $null",
        "$browserName = $null",
        "$browserSource = $null",
        "$selectedProgId = $null",
        "foreach ($candidateProgId in ($progIds | Select-Object -Unique)) {",
        "  foreach ($key in @(",
        "    \"HKCU:\\Software\\Classes\\$candidateProgId\\shell\\open\\command\",",
        "    \"HKLM:\\Software\\Classes\\$candidateProgId\\shell\\open\\command\",",
        "    \"Registry::HKEY_CLASSES_ROOT\\$candidateProgId\\shell\\open\\command\"",
        "  )) {",
        "    $commandText = Get-AtriumDefaultValue $key",
        "    $candidatePath = Resolve-AtriumExecutableFromCommand $commandText",
        "    if ($candidatePath -and (Test-Path -LiteralPath $candidatePath)) {",
        "      $browserPath = $candidatePath",
        "      $browserName = [System.IO.Path]::GetFileNameWithoutExtension($browserPath)",
        "      $browserSource = 'defaultBrowserRegistry'",
        "      $selectedProgId = $candidateProgId",
        "      break",
        "    }",
        "  }",
        "  if ($browserPath) { break }",
        "}",
        "$row = $null",
        "if ($browserPath) {",
        "  $proc = Start-Process -FilePath $browserPath -ArgumentList $argumentList -PassThru",
        "  $startedProcessId = if ($proc) { $proc.Id } else { $null }",
        "  $exeName = [System.IO.Path]::GetFileName($browserPath)",
        "  for ($attempt = 0; $attempt -lt 10 -and -not $row; $attempt++) {",
        "    Start-Sleep -Milliseconds 250",
        "    $candidate = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {",
        "      ($_.Name -eq $exeName) -and (",
        "        ($startedProcessId -and $_.ProcessId -eq $startedProcessId) -or",
        "        ($_.CommandLine -and $_.CommandLine.IndexOf($url, [StringComparison]::OrdinalIgnoreCase) -ge 0)",
        "      )",
        "    } | Sort-Object CreationDate -Descending | Select-Object -First 1)",
        "    if ($candidate) {",
        "      $row = [PSCustomObject]@{ processId=[int]$candidate.ProcessId; processName=$candidate.Name; launchPath=$browserPath; browserName=$browserName; source=$browserSource; startedProcessId=$startedProcessId; processVerified=$true; progId=$selectedProgId }",
        "    }",
        "  }",
        "  if (-not $row -and $proc) {",
        "    $row = [PSCustomObject]@{ processId=[int]$proc.Id; processName=$proc.ProcessName; launchPath=$browserPath; browserName=$browserName; source='startProcess'; startedProcessId=$startedProcessId; processVerified=$false; progId=$selectedProgId }",
        "  }",
        "} else {",
        "  $proc = Start-Process -FilePath $url -PassThru",
        "  if ($proc) {",
        "    $row = [PSCustomObject]@{ processId=[int]$proc.Id; processName=$proc.ProcessName; launchPath=$url; browserName=$null; source='shellAssociation'; startedProcessId=$proc.Id; processVerified=$false; progId=$null }",
        "  }",
        "}",
        "if ($row) { $row | ConvertTo-Json -Compress }",
    ])


def execute_browser_open(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("browser.open requires url")
    url = url.strip()
    profile = browser_profile_from_args(args)
    if profile == "user":
        if _is_windows():
            result = _run_windows_powershell(_windows_user_browser_open_script(url), run_process, timeout=12.0)
            launched = _json_rows_from_stdout(result)
            launched_row = launched[0] if launched else {}
            result.update({
                "profile": profile,
                "profileKind": "user",
                "url": url,
                "browserApp": launched_row.get("browserName"),
                "browserAppPath": launched_row.get("launchPath") if launched_row.get("source") != "shellAssociation" else None,
                "processId": launched_row.get("processId"),
                "startedProcessId": launched_row.get("startedProcessId"),
                "processName": launched_row.get("processName"),
                "processVerified": launched_row.get("processVerified"),
                "source": launched_row.get("source"),
                "progId": launched_row.get("progId"),
                "platform": sys.platform,
            })
            return result
        result = run_process(["open", url], timeout=10.0)
        result.update({"profile": profile, "profileKind": "user", "url": url})
        return result

    app = _browser_app_candidate()
    if app is None:
        return {
            "returnCode": 127,
            "stdout": "",
            "stderr": "No supported Chromium browser app found for isolated browser profiles",
            "profile": profile,
            "profileKind": "isolated",
            "url": url,
        }
    app_name, app_path = app
    data_dir = browser_profile_data_dir(profile)
    assert data_dir is not None
    data_dir.mkdir(parents=True, exist_ok=True)
    if _is_windows():
        argument_values = [
            f"--user-data-dir={data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            url,
        ]
        argument_list = _windows_argument_list(argument_values)
        script = "\n".join([
            f"$launchPath = {_ps_string(str(app_path))}",
            f"$profileDir = {_ps_string(str(data_dir))}",
            f"$argumentList = {_ps_string(argument_list)}",
            f"$proc = Start-Process -FilePath {_ps_string(str(app_path))} -ArgumentList $argumentList -PassThru",
            "$startedProcessId = if ($proc) { $proc.Id } else { $null }",
            "$row = $null",
            "$exeName = [System.IO.Path]::GetFileName($launchPath)",
            "for ($attempt = 0; $attempt -lt 10 -and -not $row; $attempt++) {",
            "  Start-Sleep -Milliseconds 250",
            "  $candidate = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {",
            "    ($_.Name -eq $exeName) -and ($_.CommandLine -and $_.CommandLine.IndexOf($profileDir, [StringComparison]::OrdinalIgnoreCase) -ge 0)",
            "  } | Sort-Object CreationDate -Descending | Select-Object -First 1)",
            "  if ($candidate) {",
            "    $row = [PSCustomObject]@{ processId=[int]$candidate.ProcessId; processName=$candidate.Name; launchPath=$launchPath; source='profileProcessLookup'; startedProcessId=$startedProcessId; profileVerified=$true }",
            "  }",
            "}",
            "if ($row) { $row | ConvertTo-Json -Compress }",
        ])
        result = _run_windows_powershell(script, run_process, timeout=10.0)
        launched = _json_rows_from_stdout(result)
        launched_row = launched[0] if launched else {}
        process_id = launched_row.get("processId")
        result.update({
            "profile": profile,
            "profileKind": "isolated",
            "isOwnProfile": profile == "atrium",
            "userDataDir": str(data_dir),
            "browserApp": app_name,
            "browserAppPath": str(app_path),
            "processId": process_id,
            "processName": launched_row.get("processName"),
            "startedProcessId": launched_row.get("startedProcessId"),
            "profileVerified": launched_row.get("profileVerified"),
            "source": launched_row.get("source"),
            "url": url,
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0:
            if not process_id:
                result["returnCode"] = 1
                result["stderr"] = "isolated browser profile process was not found after launch"
            elif launched_row.get("profileVerified") is not True:
                result["returnCode"] = 1
                result["stderr"] = "isolated browser profile process did not verify requested profile"
        return result
    command = [
        "open",
        "-na",
        app_name,
        "--args",
        f"--user-data-dir={data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]
    result = run_process(command, timeout=10.0)
    result.update({
        "profile": profile,
        "profileKind": "isolated",
        "isOwnProfile": profile == "atrium",
        "userDataDir": str(data_dir),
        "browserApp": app_name,
        "browserAppPath": str(app_path),
        "url": url,
    })
    return result


def _applescript_string(value: Any) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _app_target(args: dict[str, Any]) -> dict[str, str | int | None]:
    raw_path = args.get("path") or args.get("appPath")
    raw_bundle = args.get("bundleId") or args.get("bundle_id")
    raw_name = args.get("appName") or args.get("name") or args.get("app")
    raw_process_id = _process_id_arg(args)
    path = str(raw_path).strip() if raw_path is not None and str(raw_path).strip() else None
    bundle_id = str(raw_bundle).strip() if raw_bundle is not None and str(raw_bundle).strip() else None
    name = str(raw_name).strip() if raw_name is not None and str(raw_name).strip() else None
    process_id = _coerce_positive_process_id(raw_process_id)
    if not (path or bundle_id or name or process_id):
        raise ValueError("desktop app tools require appName, bundleId, path, or processId")
    return {"path": path, "bundleId": bundle_id, "name": name, "processId": process_id}


def _process_id_arg(args: dict[str, Any]) -> Any:
    raw = args.get("processId")
    if raw is not None and str(raw).strip():
        return raw
    return args.get("pid")


def _coerce_positive_process_id(raw_process_id: Any, *, label: str = "processId") -> int | None:
    if raw_process_id is None or not str(raw_process_id).strip():
        return None
    if isinstance(raw_process_id, bool):
        raise ValueError(f"{label} must be a positive integer")
    if isinstance(raw_process_id, int):
        process_id = raw_process_id
    elif isinstance(raw_process_id, float):
        if not math.isfinite(raw_process_id) or not raw_process_id.is_integer():
            raise ValueError(f"{label} must be a positive integer")
        process_id = int(raw_process_id)
    else:
        text = str(raw_process_id).strip()
        if re.fullmatch(r"\+?\d+", text):
            process_id = int(text)
        elif re.fullmatch(r"\+?\d+\.0+", text):
            process_id = int(text.split(".", 1)[0])
        else:
            raise ValueError(f"{label} must be a positive integer")
    if process_id <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return process_id


def _app_target_label(target: dict[str, str | int | None]) -> str:
    return str(target.get("name") or target.get("bundleId") or target.get("path") or target.get("processId") or "app")


def _windows_process_needle(target: dict[str, str | int | None]) -> str:
    if target.get("path"):
        return Path(str(target["path"])).stem
    label = _app_target_label(target)
    return label[:-4] if label.lower().endswith(".exe") else label


def _windows_start_menu_shortcuts(*, query: str = "", max_items: int = 80) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    needle = query.strip().lower()
    for base in _windows_start_menu_dirs():
        if not base.exists():
            continue
        for shortcut in sorted(base.rglob("*.lnk")):
            key = str(shortcut).lower()
            if key in seen:
                continue
            name = shortcut.stem
            haystack = f"{name} {shortcut}".lower()
            if needle and needle not in haystack:
                continue
            seen.add(key)
            rows.append({"name": name, "path": str(shortcut), "kind": "shortcut"})
            if len(rows) >= max_items:
                return rows
    return rows


def _windows_find_start_menu_shortcut(app_name: str) -> Path | None:
    needle = app_name.strip().lower()
    if not needle:
        return None
    matches = _windows_start_menu_shortcuts(query=needle, max_items=200)
    exact = [row for row in matches if row.get("name", "").strip().lower() == needle]
    selected = exact[0] if exact else (matches[0] if matches else None)
    if not selected:
        return None
    path = Path(selected["path"])
    return path if path.exists() else None


def _windows_launch_path(target: dict[str, str | int | None]) -> str | None:
    raw_path = target.get("path")
    if raw_path:
        path = Path(raw_path)
        if path.exists():
            return str(path)
        if path.suffix.lower() == ".exe":
            return str(path)
    raw_name = target.get("name")
    if raw_name:
        shortcut = _windows_find_start_menu_shortcut(raw_name)
        if shortcut:
            return str(shortcut)
        return raw_name
    raw_bundle = target.get("bundleId")
    return str(raw_bundle) if raw_bundle else None


def _app_tell_prefix(target: dict[str, str | int | None]) -> str:
    if target.get("bundleId"):
        return f"tell application id {_applescript_string(target['bundleId'])}"
    return f"tell application {_applescript_string(_app_target_label(target))}"


def _macos_foreground_verification_script(target: dict[str, str | int | None]) -> str | None:
    process_id = str(target.get("processId") or "").strip()
    name = str(target.get("name") or "").strip()
    if not process_id and not name:
        return None
    return "\n".join([
        f"set targetPid to {_applescript_string(process_id)}",
        f"set targetName to {_applescript_string(name)}",
        "tell application \"System Events\"",
        "  set targetProcess to missing value",
        "  if targetPid is not \"\" then",
        "    try",
        "      set targetProcess to first application process whose unix id is (targetPid as integer)",
        "    end try",
        "  end if",
        "  if targetProcess is missing value and targetName is not \"\" then",
        "    try",
        "      set targetProcess to first application process whose name is targetName",
        "    end try",
        "  end if",
        "  if targetProcess is missing value then error \"window not found\"",
        "  set frontmost of targetProcess to true",
        "  delay 0.2",
        "  set activeProcess to first application process whose frontmost is true",
        "  set targetPidOut to \"\"",
        "  set activePidOut to \"\"",
        "  try",
        "    set targetPidOut to unix id of targetProcess as text",
        "  end try",
        "  try",
        "    set activePidOut to unix id of activeProcess as text",
        "  end try",
        "  set targetNameOut to name of targetProcess as text",
        "  set activeNameOut to name of activeProcess as text",
        "  set isForeground to ((targetPidOut is not \"\" and activePidOut is targetPidOut) or (activeNameOut is targetNameOut))",
        "  return \"FOREGROUND\" & tab & isForeground & tab & targetNameOut & tab & targetPidOut & tab & activeNameOut & tab & activePidOut",
        "end tell",
    ])


def _macos_verify_app_foreground(
    target: dict[str, str | int | None],
    run_process: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    script = _macos_foreground_verification_script(target)
    if script is None:
        return None
    result = _run_visual_command(run_process, ["osascript", "-e", script], timeout=10.0)
    raw_stdout = str(result.get("stdout") or "").strip()
    parts = raw_stdout.split("\t")
    foreground = len(parts) >= 2 and parts[0] == "FOREGROUND" and parts[1].strip().lower() == "true"
    result.update({
        "foreground": foreground,
        "processName": parts[2].strip() if len(parts) > 2 else target.get("name"),
        "processId": int(parts[3]) if len(parts) > 3 and parts[3].strip().isdigit() else target.get("processId"),
        "activeProcessName": parts[4].strip() if len(parts) > 4 else None,
        "activeProcessId": int(parts[5]) if len(parts) > 5 and parts[5].strip().isdigit() else None,
        "platform": sys.platform,
        "method": "accessibility_foreground_verify",
    })
    if result.get("returnCode") == 0:
        if parts[:1] != ["FOREGROUND"]:
            result["returnCode"] = 1
            result["stderr"] = "window activation did not return foreground verification metadata"
        elif not foreground:
            result["returnCode"] = 1
            result["stderr"] = "window did not become foreground"
    return result


def _macos_native_activate_app(
    target: dict[str, str | int | None],
    run_process: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    helper = _ensure_activate_helper()
    if helper is None:
        return None
    command = [
        str(helper),
        str(target.get("processId") or ""),
        str(target.get("name") or ""),
        str(target.get("bundleId") or ""),
        str(target.get("path") or ""),
    ]
    result = run_process(command, timeout=5.0)
    parsed = _json_value_from_stdout(result.get("stdout"))
    row = parsed if isinstance(parsed, dict) else {}
    result.update({
        "appName": target.get("name"),
        "bundleId": row.get("bundleId") or target.get("bundleId"),
        "path": row.get("path") or target.get("path"),
        "processId": row.get("processId") or target.get("processId"),
        "requestedProcessId": target.get("processId"),
        "processName": row.get("processName") or target.get("name"),
        "foreground": row.get("foreground"),
        "activeProcessId": row.get("activeProcessId"),
        "activeProcessName": row.get("activeProcessName"),
        "targetFound": row.get("targetFound"),
        "axTrusted": row.get("axTrusted"),
        "axFrontmostSet": row.get("axFrontmostSet"),
        "axFrontmostError": row.get("axFrontmostError"),
        "axRaisedWindow": row.get("axRaisedWindow"),
        "axRaiseError": row.get("axRaiseError"),
        "axWindowCount": row.get("axWindowCount"),
        "nsActivated": row.get("nsActivated"),
        "platform": sys.platform,
        "method": "native_appkit_activation",
        "activationBackend": "appkit_ax",
        "foregroundVerification": {
            "foreground": row.get("foreground"),
            "processId": row.get("processId") or target.get("processId"),
            "processName": row.get("processName") or target.get("name"),
            "activeProcessId": row.get("activeProcessId"),
            "activeProcessName": row.get("activeProcessName"),
            "targetFound": row.get("targetFound"),
            "method": "native_appkit_activation",
        },
    })
    if result.get("returnCode") == 0:
        if not row:
            result["returnCode"] = 1
            result["stderr"] = "window activation did not return foreground verification metadata"
        elif row.get("foreground") is not True:
            result["returnCode"] = 1
            result["stderr"] = str(row.get("error") or "window did not become foreground")
    elif row.get("error") and not result.get("stderr"):
        result["stderr"] = str(row["error"])
    return result


def _app_bundle_info(app_path: Path) -> dict[str, str | None]:
    info_path = app_path / "Contents" / "Info.plist"
    info: dict[str, Any] = {}
    try:
        with info_path.open("rb") as f:
            loaded = plistlib.load(f)
            if isinstance(loaded, dict):
                info = loaded
    except Exception:
        info = {}
    name = (
        info.get("CFBundleDisplayName")
        or info.get("CFBundleName")
        or info.get("CFBundleExecutable")
        or app_path.stem
    )
    return {
        "name": str(name) if name else app_path.stem,
        "path": str(app_path),
        "bundleId": str(info.get("CFBundleIdentifier")) if info.get("CFBundleIdentifier") else None,
    }


def _json_value_from_stdout(raw_stdout: Any) -> Any:
    raw = str(raw_stdout or "").strip()
    if not raw:
        return None
    candidates = [raw, *(line.strip() for line in reversed(raw.splitlines()) if line.strip())]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    for idx, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            parsed, _end = decoder.raw_decode(raw[idx:].strip())
        except json.JSONDecodeError:
            continue
        return parsed
    return None


def _json_rows_from_stdout(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("returnCode") != 0:
        return []
    parsed = _json_value_from_stdout(result.get("stdout"))
    try:
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except TypeError:
        return []
    return []


def _windows_list_apps(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    include_installed = bool(args.get("includeInstalled", True))
    include_running = bool(args.get("includeRunning", True))
    query = str(args.get("query") or args.get("search") or "").strip().lower()
    try:
        max_items = max(1, min(int(args.get("limit") or 80), 300))
    except (TypeError, ValueError):
        max_items = 80

    running: list[dict[str, Any]] = []
    installed: list[dict[str, Any]] = []
    running_result: dict[str, Any] | None = None
    installed_result: dict[str, Any] | None = None
    if include_running:
        script = "\n".join([
            "$rows = @(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle } | ForEach-Object {",
            "  $path = $null; try { $path = $_.Path } catch {}",
            "  [PSCustomObject]@{ name=$_.ProcessName; title=$_.MainWindowTitle; processId=$_.Id; path=$path }",
            "})",
            "$rows | ConvertTo-Json -Compress -Depth 4",
        ])
        running_result = _run_windows_powershell(script, run_process, timeout=10.0)
        running = _json_rows_from_stdout(running_result)
        if query:
            running = [
                row
                for row in running
                if query in f"{row.get('name') or ''} {row.get('title') or ''} {row.get('path') or ''}".lower()
            ]
    if include_installed:
        script = "\n".join([
            "$roots = @(",
            "  'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',",
            "  'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',",
            "  'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'",
            ")",
            "$rows = @(Get-ItemProperty $roots -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName } | ForEach-Object {",
            "  [PSCustomObject]@{ name=$_.DisplayName; path=$_.InstallLocation; version=$_.DisplayVersion; publisher=$_.Publisher }",
            "})",
            "$rows | ConvertTo-Json -Compress -Depth 4",
        ])
        installed_result = _run_windows_powershell(script, run_process, timeout=15.0)
        installed = _json_rows_from_stdout(installed_result)
        if query:
            installed = [
                row
                for row in installed
                if query in f"{row.get('name') or ''} {row.get('publisher') or ''} {row.get('path') or ''}".lower()
            ]
        shortcut_rows = _windows_start_menu_shortcuts(query=query, max_items=max_items)
        installed = [*shortcut_rows, *({**row, "kind": row.get("kind") or "registry"} for row in installed)]
        deduped_installed: list[dict[str, Any]] = []
        seen_installed: set[str] = set()
        for row in installed:
            key = f"{row.get('name') or ''}\0{row.get('path') or ''}".lower()
            if key in seen_installed:
                continue
            seen_installed.add(key)
            deduped_installed.append(row)
            if len(deduped_installed) >= max_items:
                break
        installed = deduped_installed
    running_ok = running_result is not None and running_result.get("returnCode") == 0
    installed_ok = installed_result is not None and (installed_result.get("returnCode") == 0 or bool(installed))
    no_discovery_requested = not include_running and not include_installed
    return_code = 0 if no_discovery_requested or running_ok or installed_ok else (
        running_result or installed_result or {"returnCode": 0}
    ).get("returnCode", 0)
    stderr_parts = [
        str(item.get("stderr") or "")
        for item in (running_result, installed_result)
        if item is not None and item.get("stderr")
    ]
    installed_error = None
    if installed_result is not None and installed_result.get("returnCode") != 0:
        installed_error = str(installed_result.get("stderr") or installed_result.get("stdout") or "installed app registry discovery failed")
    return {
        "returnCode": return_code,
        "running": running[:max_items],
        "installed": installed,
        "installedCount": len(installed),
        "query": query,
        "stderr": "\n".join(part for part in stderr_parts if part),
        "runningReturnCode": None if running_result is None else running_result.get("returnCode"),
        "installedReturnCode": None if installed_result is None else installed_result.get("returnCode"),
        "installedError": installed_error,
        "platform": sys.platform,
    }


def _installed_apps(*, query: str = "", max_items: int = 80) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    seen: set[str] = set()
    needle = query.strip().lower()
    for base in _APP_SEARCH_DIRS:
        if not base.exists():
            continue
        for app_path in sorted(base.glob("*.app")):
            key = str(app_path.resolve())
            if key in seen:
                continue
            info = _app_bundle_info(app_path)
            haystack = f"{info.get('name') or ''} {info.get('bundleId') or ''} {info.get('path') or ''}".lower()
            if needle and needle not in haystack:
                continue
            seen.add(key)
            rows.append(info)
            if len(rows) >= max_items:
                return rows
    return rows


def _macos_running_apps_script() -> str:
    return "\n".join([
        "on replaceText(theText, oldText, newText)",
        "  set AppleScript's text item delimiters to oldText",
        "  set theItems to text items of theText",
        "  set AppleScript's text item delimiters to newText",
        "  set theText to theItems as text",
        "  set AppleScript's text item delimiters to \"\"",
        "  return theText",
        "end replaceText",
        "",
        "on cleanText(valueText)",
        "  try",
        "    set outText to valueText as text",
        "  on error",
        "    return \"\"",
        "  end try",
        "  set outText to my replaceText(outText, tab, \" \")",
        "  set outText to my replaceText(outText, return, \" \")",
        "  set outText to my replaceText(outText, linefeed, \" \")",
        "  return outText",
        "end cleanText",
        "",
        "on joinList(theList, delimiterText)",
        "  set AppleScript's text item delimiters to delimiterText",
        "  set outText to theList as text",
        "  set AppleScript's text item delimiters to \"\"",
        "  return outText",
        "end joinList",
        "",
        "set outputRows to {}",
        "tell application \"System Events\"",
        "  set appProcesses to application processes whose background only is false",
        "  repeat with appProcess in appProcesses",
        "    set nameText to \"\"",
        "    set processIdText to \"\"",
        "    set titleText to \"\"",
        "    set bundleIdText to \"\"",
        "    set frontmostText to \"\"",
        "    try",
        "      set nameText to my cleanText(name of appProcess)",
        "    end try",
        "    try",
        "      set processIdText to my cleanText(unix id of appProcess)",
        "    end try",
        "    try",
        "      if (count of windows of appProcess) is greater than 0 then",
        "        set titleText to my cleanText(name of window 1 of appProcess)",
        "      end if",
        "    end try",
        "    try",
        "      set bundleIdText to my cleanText(bundle identifier of appProcess)",
        "    end try",
        "    try",
        "      set frontmostText to my cleanText(frontmost of appProcess)",
        "    end try",
        "    set end of outputRows to my joinList({\"ROW\", nameText, processIdText, titleText, bundleIdText, frontmostText}, tab)",
        "  end repeat",
        "end tell",
        "return my joinList(outputRows, linefeed)",
    ])


def _parse_macos_running_apps_stdout(stdout: Any) -> list[dict[str, Any] | str]:
    raw = str(stdout or "").strip()
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[0] != "ROW":
            continue
        name = _desktop_text_field({"name": parts[1]}, "name")
        item: dict[str, Any] = {"name": name}
        pid_text = _desktop_text_field({"processId": parts[2]}, "processId")
        if pid_text.isdigit():
            item["processId"] = int(pid_text)
        if len(parts) > 3:
            title = _desktop_text_field({"title": parts[3]}, "title")
            if title:
                item["title"] = title
        if len(parts) > 4:
            bundle_id = _desktop_text_field({"bundleId": parts[4]}, "bundleId")
            if bundle_id:
                item["bundleId"] = bundle_id
        if len(parts) > 5:
            frontmost = _desktop_text_field({"frontmost": parts[5]}, "frontmost")
            if frontmost:
                item["frontmost"] = frontmost.lower() == "true"
        rows.append(item)
    if rows:
        return rows
    return [part.strip() for part in raw.replace("\r", "\n").replace(", ", "\n").splitlines() if part.strip()]


def _desktop_app_row_haystack(row: dict[str, Any] | str) -> str:
    if isinstance(row, dict):
        return " ".join(
            str(row.get(key) or "")
            for key in ("name", "title", "bundleId", "path", "processId")
        )
    return str(row)


def _running_apps(run_process: Callable[..., dict[str, Any]]) -> tuple[list[dict[str, Any] | str], dict[str, Any]]:
    helper = _ensure_apps_helper()
    if helper is not None:
        result = run_process([str(helper)], timeout=5.0)
        parsed = _json_value_from_stdout(result.get("stdout"))
        if result.get("returnCode") == 0 and isinstance(parsed, list):
            rows = [item for item in parsed if isinstance(item, dict)]
            result.update({"method": "native_nsworkspace_apps", "platform": sys.platform})
            return rows, result
    script = _macos_running_apps_script()
    result = run_process(["osascript", "-e", script], timeout=10.0)
    result["method"] = result.get("method") or "osascript"
    return _parse_macos_running_apps_stdout(result.get("stdout")), result


def execute_list_apps(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    if _is_windows():
        return _windows_list_apps(args, run_process)
    include_installed = bool(args.get("includeInstalled", True))
    include_running = bool(args.get("includeRunning", True))
    query = str(args.get("query") or args.get("search") or "").strip()
    try:
        max_items = max(1, min(int(args.get("limit") or 80), 300))
    except (TypeError, ValueError):
        max_items = 80

    running: list[str] = []
    running_result: dict[str, Any] | None = None
    if include_running:
        running, running_result = _running_apps(run_process)
    installed = _installed_apps(query=query, max_items=max_items) if include_installed else []
    if query and running:
        needle = query.lower()
        running = [row for row in running if needle in _desktop_app_row_haystack(row).lower()]
    return {
        "returnCode": 0 if running_result is None else running_result.get("returnCode", 0),
        "running": running,
        "installed": installed,
        "installedCount": len(installed),
        "query": query,
        "stderr": "" if running_result is None else running_result.get("stderr", ""),
        "runningMethod": None if running_result is None else running_result.get("method"),
        "runningReturnCode": None if running_result is None else running_result.get("returnCode"),
        "platform": sys.platform,
    }


def execute_open_app(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    target = _app_target(args)
    if not (target.get("name") or target.get("bundleId") or target.get("path")):
        raise ValueError("desktop.open_app requires appName, bundleId, or path")
    if _is_windows():
        app = _windows_launch_path(target)
        if not app:
            raise ValueError("desktop.open_app requires appName, bundleId, or path")
        open_target = args.get("target") or args.get("file") or args.get("url")
        raw_app_args = args.get("arguments") if isinstance(args.get("arguments"), list) else args.get("args")
        argument_values: list[Any] = []
        if open_target is not None and str(open_target).strip():
            argument_values.append(str(open_target).strip())
        if isinstance(raw_app_args, list):
            argument_values.extend(str(item) for item in raw_app_args)
        argument_list = _windows_argument_list(argument_values) if argument_values else ""
        needle = _windows_process_needle(target)
        script_lines = [
            f"$launchPath = {_ps_string(app)}",
            f"$needle = {_ps_string(needle)}",
        ]
        if argument_values:
            script_lines.append(f"$argumentList = {_ps_string(argument_list)}")
        script_lines.append(f"$proc = Start-Process -FilePath {_ps_string(app)} -PassThru")
        if argument_values:
            script_lines[-1] += " -ArgumentList $argumentList"
        script_lines.extend([
            "$row = $null",
            "if ($proc) {",
            "  $row = [PSCustomObject]@{ processId=$proc.Id; processName=$proc.ProcessName; launchPath=$launchPath; source='startProcess' }",
            "}",
            "if ((-not $row -or -not $row.processId) -or $launchPath.ToLowerInvariant().EndsWith('.lnk')) {",
            "  Start-Sleep -Milliseconds 500",
            "  $candidate = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {",
            "    ($_.ProcessName -like \"*$needle*\") -or ($_.MainWindowTitle -like \"*$needle*\")",
            "  } | Sort-Object StartTime -Descending -ErrorAction SilentlyContinue | Select-Object -First 1)",
            "  if ($candidate) {",
            "    $row = [PSCustomObject]@{ processId=$candidate.Id; processName=$candidate.ProcessName; launchPath=$launchPath; source='processLookup' }",
            "  }",
            "}",
            "if ($row) { $row | ConvertTo-Json -Compress }",
        ])
        result = _run_windows_powershell(
            "\n".join(script_lines),
            run_process,
            timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)),
        )
        launched = _json_rows_from_stdout(result)
        launched_row = launched[0] if launched else {}
        process_id = launched_row.get("processId")
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "launchPath": app,
            "processId": process_id,
            "processName": launched_row.get("processName"),
            "processVerified": bool(process_id),
            "source": launched_row.get("source"),
            "target": str(open_target).strip() if open_target is not None else None,
            "platform": sys.platform,
        })
        if not process_id and result.get("returnCode") == 0:
            result["returnCode"] = 1
            result["stderr"] = "desktop app process was not found after launch"
        return result
    command = ["open"]
    if args.get("newInstance") or args.get("new_instance"):
        command.append("-n")
    if target.get("bundleId"):
        command.extend(["-b", str(target["bundleId"])])
    elif target.get("path"):
        command.extend(["-a", str(target["path"])])
    else:
        command.extend(["-a", str(target["name"])])

    open_target = args.get("target") or args.get("file") or args.get("url")
    if open_target is not None and str(open_target).strip():
        command.append(str(open_target).strip())
    raw_app_args = args.get("arguments") if isinstance(args.get("arguments"), list) else args.get("args")
    if isinstance(raw_app_args, list) and raw_app_args:
        command.append("--args")
        command.extend(str(item) for item in raw_app_args)
    result = run_process(command, timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)))
    result.update({
        "appName": target.get("name"),
        "bundleId": target.get("bundleId"),
        "path": target.get("path"),
        "target": str(open_target).strip() if open_target is not None else None,
    })
    return result


def execute_activate_app(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    target = _app_target(args)
    if _is_windows():
        process_id = target.get("processId")
        script_lines = [
            "$sig = '[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd); [DllImport(\"user32.dll\")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow); [DllImport(\"user32.dll\")] public static extern bool BringWindowToTop(IntPtr hWnd); [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow(); [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId); [DllImport(\"kernel32.dll\")] public static extern uint GetCurrentThreadId(); [DllImport(\"user32.dll\")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);'",
            "Add-Type -MemberDefinition $sig -Name Win32Window -Namespace ATRIUM",
        ]
        if process_id:
            script_lines.extend([
                f"$proc = Get-Process -Id {int(process_id)} -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }} | Select-Object -First 1",
            ])
        else:
            needle = _windows_process_needle(target)
            script_lines.extend([
                f"$needle = {_ps_string(needle)}",
                "$proc = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and (($_.ProcessName -like \"*$needle*\") -or ($_.MainWindowTitle -like \"*$needle*\")) } | Select-Object -First 1",
            ])
        script_lines.extend([
            "if (-not $proc) { Write-Error \"window not found\"; exit 1 }",
            "$targetPid = [uint32]0",
            "$targetThread = [ATRIUM.Win32Window]::GetWindowThreadProcessId($proc.MainWindowHandle, [ref]$targetPid)",
            "$foregroundWindow = [ATRIUM.Win32Window]::GetForegroundWindow()",
            "$foregroundPid = [uint32]0",
            "$foregroundThread = if ($foregroundWindow -ne [IntPtr]::Zero) { [ATRIUM.Win32Window]::GetWindowThreadProcessId($foregroundWindow, [ref]$foregroundPid) } else { [uint32]0 }",
            "$currentThread = [ATRIUM.Win32Window]::GetCurrentThreadId()",
            "$attachedCurrent = $false",
            "$attachedForeground = $false",
            "if ($targetThread -and $currentThread -and $targetThread -ne $currentThread) { $attachedCurrent = [ATRIUM.Win32Window]::AttachThreadInput($currentThread, $targetThread, $true) }",
            "if ($targetThread -and $foregroundThread -and $foregroundThread -ne $targetThread) { $attachedForeground = [ATRIUM.Win32Window]::AttachThreadInput($foregroundThread, $targetThread, $true) }",
            "$showWindow = $false",
            "$bringToTop = $false",
            "$setForeground = $false",
            "try {",
            "  $showWindow = [ATRIUM.Win32Window]::ShowWindowAsync($proc.MainWindowHandle, 9)",
            "  $bringToTop = [ATRIUM.Win32Window]::BringWindowToTop($proc.MainWindowHandle)",
            "  $setForeground = [ATRIUM.Win32Window]::SetForegroundWindow($proc.MainWindowHandle)",
            "} finally {",
            "  if ($attachedForeground) { [ATRIUM.Win32Window]::AttachThreadInput($foregroundThread, $targetThread, $false) | Out-Null }",
            "  if ($attachedCurrent) { [ATRIUM.Win32Window]::AttachThreadInput($currentThread, $targetThread, $false) | Out-Null }",
            "}",
            "Start-Sleep -Milliseconds 200",
            "$activeWindow = [ATRIUM.Win32Window]::GetForegroundWindow()",
            "$activePid = [uint32]0",
            "$activeThread = if ($activeWindow -ne [IntPtr]::Zero) { [ATRIUM.Win32Window]::GetWindowThreadProcessId($activeWindow, [ref]$activePid) } else { [uint32]0 }",
            "$isForeground = ($activeWindow -eq $proc.MainWindowHandle) -or ($activePid -eq [uint32]$proc.Id)",
            "[PSCustomObject]@{ name=$proc.ProcessName; title=$proc.MainWindowTitle; processId=$proc.Id; foreground=$isForeground; activeProcessId=[int]$activePid; activeThreadId=[int]$activeThread; setForeground=$setForeground; bringToTop=$bringToTop; showWindow=$showWindow; attachedCurrent=$attachedCurrent; attachedForeground=$attachedForeground } | ConvertTo-Json -Compress",
        ])
        script = "\n".join(script_lines)
        result = _run_windows_powershell(script, run_process, timeout=10.0)
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "processId": row.get("processId") or process_id,
            "requestedProcessId": process_id,
            "processName": row.get("name"),
            "title": row.get("title"),
            "foreground": row.get("foreground"),
            "activeProcessId": row.get("activeProcessId"),
            "activeThreadId": row.get("activeThreadId"),
            "setForeground": row.get("setForeground"),
            "bringToTop": row.get("bringToTop"),
            "showWindow": row.get("showWindow"),
            "attachedCurrent": row.get("attachedCurrent"),
            "attachedForeground": row.get("attachedForeground"),
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0:
            if not row:
                result["returnCode"] = 1
                result["stderr"] = "window activation did not return foreground verification metadata"
            elif row.get("foreground") is not True:
                result["returnCode"] = 1
                result["stderr"] = "window did not become foreground"
        return result
    native_result = _macos_native_activate_app(target, run_process)
    if native_result is not None:
        if (
            native_result.get("returnCode") == 0
            or target.get("processId")
            or native_result.get("targetFound") is True
        ):
            return native_result

    if target.get("processId") and not (target.get("name") or target.get("bundleId") or target.get("path")):
        result = _macos_verify_app_foreground(target, run_process)
        if result is None:
            result = {
                "command": ["osascript"],
                "returnCode": 1,
                "stdout": "",
                "stderr": "desktop app tools require appName, bundleId, path, or processId",
            }
        verification = dict(result)
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "processId": result.get("processId") or target.get("processId"),
            "foregroundVerification": verification,
        })
        return result

    script = f"{_app_tell_prefix(target)} to activate"
    result = run_process(["osascript", "-e", script], timeout=10.0)
    result.update({"appName": target.get("name"), "bundleId": target.get("bundleId"), "path": target.get("path")})
    if result.get("returnCode") != 0:
        fallback_command = ["open"]
        if target.get("bundleId"):
            fallback_command.extend(["-b", str(target["bundleId"])])
        elif target.get("path"):
            fallback_command.extend(["-a", str(target["path"])])
        else:
            fallback_command.extend(["-a", str(target["name"])])
        fallback = run_process(fallback_command, timeout=10.0)
        result["fallbackResult"] = fallback
        if fallback.get("returnCode") == 0:
            result["returnCode"] = 0
            result["activationFallback"] = "open"
            result["stderr"] = ""
    if result.get("returnCode") == 0:
        verification = _macos_verify_app_foreground(target, run_process)
        if verification is not None:
            result["foregroundVerification"] = verification
            result["foreground"] = verification.get("foreground")
            result["activeProcessId"] = verification.get("activeProcessId")
            result["activeProcessName"] = verification.get("activeProcessName")
            if verification.get("processId") is not None:
                result["processId"] = verification.get("processId")
            if verification.get("processName"):
                result["processName"] = verification.get("processName")
            if verification.get("returnCode") != 0:
                result["returnCode"] = verification.get("returnCode")
                result["stderr"] = verification.get("stderr") or "window did not become foreground"
    return result


def execute_quit_app(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    target = _app_target(args)
    force = bool(args.get("force") or args.get("forceQuit") or args.get("force_quit"))
    process_id = target.get("processId")
    if _is_windows():
        delay_ms = int(max(0.0, min(float(args.get("forceDelaySeconds") or 1.0), 10.0)) * 1000)
        script_lines = []
        if process_id:
            script_lines.append(f"$procs = @(Get-Process -Id {int(process_id)} -ErrorAction SilentlyContinue)")
        else:
            needle = _windows_process_needle(target)
            script_lines.extend([
                f"$needle = {_ps_string(needle)}",
                "$procs = @(Get-Process | Where-Object { ($_.ProcessName -like \"*$needle*\") -or ($_.MainWindowTitle -like \"*$needle*\") })",
            ])
        script_lines.extend([
            "if (-not $procs) { Write-Error \"process not found\"; exit 1 }",
            "$closed = 0",
            "foreach ($proc in $procs) { if ($proc.MainWindowHandle -ne 0 -and $proc.CloseMainWindow()) { $closed++ } }",
            f"Start-Sleep -Milliseconds {delay_ms}",
            *(
                [
                    "foreach ($proc in $procs) {",
                    "  try { if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } } catch {}",
                    "}",
                    "Start-Sleep -Milliseconds 200",
                ]
                if force
                else []
            ),
            "$remaining = 0",
            "foreach ($proc in $procs) {",
            "  try { if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) { $remaining++ } } catch {}",
            "}",
            "$quitVerified = ($remaining -eq 0)",
            "[PSCustomObject]@{ matched=$procs.Count; gracefulCloseSent=$closed; force=$" + str(force).lower() + "; remaining=$remaining; quitVerified=$quitVerified } | ConvertTo-Json -Compress",
        ])
        script = "\n".join(script_lines)
        result = _run_windows_powershell(
            script,
            run_process,
            timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)),
        )
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "processId": process_id,
            "force": force,
            "matched": row.get("matched"),
            "gracefulCloseSent": row.get("gracefulCloseSent"),
            "remaining": row.get("remaining"),
            "quitVerified": row.get("quitVerified"),
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0:
            if not row:
                result["returnCode"] = 1
                result["stderr"] = "desktop app quit did not return process verification metadata"
            elif row.get("quitVerified") is not True:
                result["returnCode"] = 1
                result["stderr"] = "desktop app process did not exit"
        return result
    if process_id:
        delay_seconds = max(0.0, min(float(args.get("forceDelaySeconds") or 1.0), 10.0))
        result = run_process(["/bin/kill", "-TERM", str(int(process_id))], timeout=5.0)
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "processId": process_id,
            "force": force,
            "signal": "TERM",
            "platform": sys.platform,
        })
        if result.get("returnCode") != 0:
            result["quitVerified"] = False
            return result
        time.sleep(delay_seconds)
        verify_result = run_process(["/bin/kill", "-0", str(int(process_id))], timeout=5.0)
        result["verifyResult"] = verify_result
        still_running = verify_result.get("returnCode") == 0
        if still_running and force:
            force_result = run_process(["/bin/kill", "-KILL", str(int(process_id))], timeout=5.0)
            result["forceResult"] = force_result
            result["forceSignal"] = "KILL"
            time.sleep(0.2)
            verify_result = run_process(["/bin/kill", "-0", str(int(process_id))], timeout=5.0)
            result["verifyResult"] = verify_result
            still_running = verify_result.get("returnCode") == 0
        result["quitVerified"] = not still_running
        if still_running:
            result["returnCode"] = 1
            result["stderr"] = "desktop app process did not exit"
        else:
            result["returnCode"] = 0
            result["stderr"] = ""
        return result
    script = f"{_app_tell_prefix(target)} to quit"
    result = run_process(["osascript", "-e", script], timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)))
    result.update({
        "appName": target.get("name"),
        "bundleId": target.get("bundleId"),
        "path": target.get("path"),
        "force": force,
    })
    if not force or result.get("returnCode") == 0:
        return result

    time.sleep(max(0.0, min(float(args.get("forceDelaySeconds") or 1.0), 10.0)))
    if target.get("name"):
        force_result = run_process(["pkill", "-x", str(target["name"])], timeout=10.0)
        result["forceResult"] = force_result
        if force_result.get("returnCode") in {0, 1}:
            result["returnCode"] = 0
            return result
        result["returnCode"] = force_result.get("returnCode")
        result["stderr"] = force_result.get("stderr") or result.get("stderr")
    return result


def _desktop_state_path() -> Path:
    return (get_settings().data_dir / "desktop-state.json").resolve()


def _read_desktop_state() -> dict[str, Any]:
    path = _desktop_state_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_desktop_state(state: dict[str, Any]) -> None:
    path = _desktop_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _desktop_snapshot_limits(args: dict[str, Any]) -> tuple[int, int]:
    max_elements = _bounded_int(args.get("maxElements"), default=120, minimum=1, maximum=500)
    max_depth = _bounded_int(args.get("maxDepth"), default=4, minimum=1, maximum=8)
    return max_elements, max_depth


def _desktop_text_field(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "missing value" else text


def _desktop_element_name(row: dict[str, Any]) -> str:
    for key in ("name", "title", "description", "value", "automationId", "className"):
        text = _desktop_text_field(row, key)
        if text:
            return text
    return ""


def _desktop_bbox_from_row(row: dict[str, Any]) -> dict[str, int] | None:
    try:
        x = int(float(row.get("x")))
        y = int(float(row.get("y")))
        width = int(float(row.get("width")))
        height = int(float(row.get("height")))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _desktop_string_list_field(row: dict[str, Any], key: str) -> list[str] | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _desktop_windows_native_actions_from_patterns(row: dict[str, Any]) -> list[str] | None:
    pattern_names = _desktop_string_list_field(row, "patterns")
    if pattern_names is None:
        return None
    normalized = {pattern.lower() for pattern in pattern_names}
    actions: set[str] = set()
    if any("invokepattern" in pattern or "togglepattern" in pattern or "selectionitempattern" in pattern for pattern in normalized):
        actions.add("click")
    if any("valuepattern" in pattern for pattern in normalized):
        actions.update({"type", "paste"})
    return sorted(actions)


def _desktop_native_supported_actions(platform: str, row: dict[str, Any]) -> list[str]:
    role = str(row.get("role") or row.get("controlType") or "").strip().lower()
    class_name = str(row.get("className") or "").strip().lower()
    actions: set[str] = set()
    if platform == "darwin":
        ax_actions = _desktop_string_list_field(row, "axActions")
        settable_attributes = _desktop_string_list_field(row, "settableAttributes")
        if ax_actions is not None or settable_attributes is not None:
            normalized_ax_actions = {item.lower() for item in ax_actions or []}
            normalized_settable = {item.lower() for item in settable_attributes or []}
            if "axpress" in normalized_ax_actions:
                actions.add("click")
            if any(action.startswith("axscroll") for action in normalized_ax_actions):
                actions.add("scroll")
            if "axvalue" in normalized_settable:
                if role in {"axscrollbar", "axvalueindicator"}:
                    actions.add("scroll")
                else:
                    actions.update({"type", "paste"})
            return sorted(actions)
        if role in {
            "axbutton",
            "axcheckbox",
            "axdisclosuretriangle",
            "axlink",
            "axmenubaritem",
            "axpopupbutton",
            "axradiobutton",
            "axtab",
        }:
            actions.add("click")
        if role in {"axcombobox", "axsearchfield", "axtextarea", "axtextfield"}:
            actions.update({"type", "paste"})
    elif platform == "win32":
        pattern_actions = _desktop_windows_native_actions_from_patterns(row)
        if pattern_actions is not None:
            return pattern_actions
        if role in {"button", "checkbox", "hyperlink", "listitem", "menuitem", "radiobutton", "tabitem", "treeitem"}:
            actions.add("click")
        if role in {"document", "edit"} or "edit" in class_name:
            actions.update({"type", "paste"})
    return sorted(actions)


def _desktop_fallback_supported_actions(bbox: dict[str, int] | None) -> list[str]:
    if bbox is None:
        return []
    return ["click", "double_click", "keypress", "paste", "scroll", "type"]


def _desktop_snapshot_result(
    *,
    platform: str,
    metadata: dict[str, Any],
    elements: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    refs: dict[str, dict[str, Any]] = {}
    for index, raw_row in enumerate(elements, start=1):
        row = dict(raw_row)
        ref = f"d{index}"
        bbox = _desktop_bbox_from_row(row)
        native_actions = _desktop_native_supported_actions(platform, row) if row.get("enabled") is not False else []
        fallback_actions = _desktop_fallback_supported_actions(bbox) if row.get("enabled") is not False else []
        supported_actions = sorted({*native_actions, *fallback_actions})
        item = {
            "ref": ref,
            "role": str(row.get("role") or row.get("controlType") or "").strip(),
            "name": _desktop_element_name(row),
            "path": str(row.get("path") or ""),
            "enabled": row.get("enabled"),
            "actionable": bool(supported_actions),
            "bboxActionable": bool(fallback_actions),
            "nativeActionable": bool(native_actions),
            "supportedActions": supported_actions,
            "nativeSupportedActions": native_actions,
            "bbox": bbox,
        }
        for key in ("subrole", "description", "value", "automationId", "className"):
            value = _desktop_text_field(row, key)
            if value:
                item[key] = value
        for key in ("axActions", "settableAttributes"):
            values = _desktop_string_list_field(row, key)
            if values is not None:
                item[key] = values
        pattern_names = _desktop_string_list_field(row, "patterns")
        if pattern_names is not None:
            item["patterns"] = pattern_names
        normalized.append(item)
        refs[ref] = {
            "path": item["path"],
            "role": item["role"],
            "name": item["name"],
            "bbox": bbox,
            "enabled": item["enabled"],
            "actionable": item["actionable"],
            "bboxActionable": item["bboxActionable"],
            "nativeActionable": item["nativeActionable"],
            "supportedActions": supported_actions,
            "nativeSupportedActions": native_actions,
            "platform": platform,
        }
        if pattern_names is not None:
            refs[ref]["patterns"] = pattern_names
        for key in ("axActions", "settableAttributes"):
            if key in item:
                refs[ref][key] = item[key]
    snapshot = {
        "appName": metadata.get("appName"),
        "processId": metadata.get("processId"),
        "title": metadata.get("title"),
        "window": metadata.get("window") if isinstance(metadata.get("window"), dict) else None,
        "elements": normalized,
    }
    _write_desktop_state({
        "platform": platform,
        "appName": metadata.get("appName"),
        "processId": metadata.get("processId"),
        "title": metadata.get("title"),
        "refs": refs,
        "updatedAt": int(time.time() * 1000),
    })
    result.update({
        "ok": result.get("returnCode") == 0,
        "platform": platform,
        "appName": metadata.get("appName"),
        "processId": metadata.get("processId"),
        "title": metadata.get("title"),
        "refCount": len(normalized),
        "actionableRefCount": sum(1 for item in normalized if item.get("actionable")),
        "bboxActionableRefCount": sum(1 for item in normalized if item.get("bboxActionable")),
        "nativeActionableRefCount": sum(1 for item in normalized if item.get("nativeActionable")),
        "refCoverage": "available" if normalized else "empty",
        "snapshot": snapshot,
    })
    if not normalized and result.get("returnCode") == 0:
        result["warning"] = "desktop.snapshot found the target app/window but no actionable accessibility/UIA elements were exposed"
    elif not any(item.get("actionable") for item in normalized) and result.get("returnCode") == 0:
        result["warning"] = "desktop.snapshot found accessibility/UIA refs, but none exposed native actions or actionable bounding boxes"
    return result


def _tab_clean(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()


def _parse_macos_desktop_snapshot_stdout(stdout: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata: dict[str, Any] = {}
    elements: list[dict[str, Any]] = []
    for line in str(stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] == "META":
            metadata[parts[1]] = _tab_clean("\t".join(parts[2:]))
            continue
        if len(parts) < 13 or parts[0] != "ROW":
            continue
        row = {
            "path": parts[1],
            "role": parts[2],
            "subrole": parts[3],
            "name": parts[4],
            "description": parts[5],
            "value": parts[6],
            "enabled": parts[7].lower() == "true" if parts[7] else None,
            "x": parts[8],
            "y": parts[9],
            "width": parts[10],
            "height": parts[11],
            "children": parts[12],
        }
        elements.append(row)
    if metadata.get("processId"):
        with contextlib.suppress(ValueError):
            metadata["processId"] = int(str(metadata["processId"]))
    window: dict[str, Any] = {}
    for key, out_key in (("windowX", "x"), ("windowY", "y"), ("windowWidth", "width"), ("windowHeight", "height")):
        value = metadata.get(key)
        if value is None or value == "":
            continue
        with contextlib.suppress(ValueError):
            window[out_key] = int(float(str(value)))
    if window:
        metadata["window"] = window
    return metadata, elements


def _macos_desktop_snapshot_script(args: dict[str, Any], *, max_elements: int, max_depth: int) -> str:
    target_name = str(args.get("appName") or args.get("name") or args.get("app") or "").strip()
    process_id_value = _coerce_positive_process_id(_process_id_arg(args))
    process_id = str(process_id_value) if process_id_value is not None else ""
    return "\n".join([
        "on replaceText(theText, oldText, newText)",
        "  set AppleScript's text item delimiters to oldText",
        "  set theItems to text items of theText",
        "  set AppleScript's text item delimiters to newText",
        "  set theText to theItems as text",
        "  set AppleScript's text item delimiters to \"\"",
        "  return theText",
        "end replaceText",
        "",
        "on cleanText(valueText)",
        "  try",
        "    set outText to valueText as text",
        "  on error",
        "    return \"\"",
        "  end try",
        "  set outText to my replaceText(outText, tab, \" \")",
        "  set outText to my replaceText(outText, return, \" \")",
        "  set outText to my replaceText(outText, linefeed, \" \")",
        "  return outText",
        "end cleanText",
        "",
        "on joinList(theList, delimiterText)",
        "  set AppleScript's text item delimiters to delimiterText",
        "  set outText to theList as text",
        "  set AppleScript's text item delimiters to \"\"",
        "  return outText",
        "end joinList",
        "",
        "on appendElement(theElement, elementPath, depthValue)",
        "  global outputRows, rowLimit, depthLimit",
        "  if (count of outputRows) is greater than or equal to rowLimit then return",
        "  tell application \"System Events\"",
        "    set roleText to \"\"",
        "    set subroleText to \"\"",
        "    set nameText to \"\"",
        "    set descriptionText to \"\"",
        "    set valueText to \"\"",
        "    set enabledText to \"\"",
        "    set xText to \"\"",
        "    set yText to \"\"",
        "    set widthText to \"\"",
        "    set heightText to \"\"",
        "    set childCountText to \"0\"",
        "    try",
        "      set roleText to my cleanText(role of theElement)",
        "    end try",
        "    try",
        "      set subroleText to my cleanText(subrole of theElement)",
        "    end try",
        "    try",
        "      set nameText to my cleanText(name of theElement)",
        "    end try",
        "    try",
        "      set descriptionText to my cleanText(description of theElement)",
        "    end try",
        "    try",
        "      set valueText to my cleanText(value of theElement)",
        "    end try",
        "    try",
        "      set enabledText to my cleanText(enabled of theElement)",
        "    end try",
        "    try",
        "      set posValue to position of theElement",
        "      set xText to my cleanText(item 1 of posValue)",
        "      set yText to my cleanText(item 2 of posValue)",
        "    end try",
        "    try",
        "      set sizeValue to size of theElement",
        "      set widthText to my cleanText(item 1 of sizeValue)",
        "      set heightText to my cleanText(item 2 of sizeValue)",
        "    end try",
        "    try",
        "      set childCountText to my cleanText(count of UI elements of theElement)",
        "    end try",
        "    set end of outputRows to my joinList({\"ROW\", elementPath, roleText, subroleText, nameText, descriptionText, valueText, enabledText, xText, yText, widthText, heightText, childCountText}, tab)",
        "    if depthValue is less than depthLimit then",
        "      try",
        "        set childCount to count of UI elements of theElement",
        "        repeat with childIndex from 1 to childCount",
        "          if (count of outputRows) is greater than or equal to rowLimit then exit repeat",
        "          my appendElement(UI element childIndex of theElement, elementPath & \".\" & childIndex, depthValue + 1)",
        "        end repeat",
        "      end try",
        "    end if",
        "  end tell",
        "end appendElement",
        "",
        "global outputRows, rowLimit, depthLimit",
        "set outputRows to {}",
        f"set rowLimit to {int(max_elements)}",
        f"set depthLimit to {int(max_depth)}",
        f"set targetName to {_applescript_string(target_name)}",
        f"set targetPid to {_applescript_string(process_id)}",
        "set hasExplicitTarget to (targetName is not \"\" or targetPid is not \"\")",
        "set headerRows to {}",
        "tell application \"System Events\"",
        "  set targetProcess to missing value",
        "  if targetPid is not \"\" then",
        "    try",
        "      set targetProcess to first application process whose unix id is (targetPid as integer)",
        "    end try",
        "  end if",
        "  if targetProcess is missing value and targetName is not \"\" then",
        "    try",
        "      set targetProcess to first application process whose name is targetName",
        "    end try",
        "  end if",
        "  if targetProcess is missing value then",
        "    if hasExplicitTarget then error \"target application process not found\"",
        "    set targetProcess to first application process whose frontmost is true",
        "  end if",
        "  set appNameText to my cleanText(name of targetProcess)",
        "  set processIdText to \"\"",
        "  try",
        "    set processIdText to my cleanText(unix id of targetProcess)",
        "  end try",
        "  set windowTitleText to \"\"",
        "  set windowXText to \"\"",
        "  set windowYText to \"\"",
        "  set windowWidthText to \"\"",
        "  set windowHeightText to \"\"",
        "  if (count of windows of targetProcess) is greater than 0 then",
        "    set targetWindow to window 1 of targetProcess",
        "    try",
        "      set windowTitleText to my cleanText(name of targetWindow)",
        "    end try",
        "    try",
        "      set winPos to position of targetWindow",
        "      set windowXText to my cleanText(item 1 of winPos)",
        "      set windowYText to my cleanText(item 2 of winPos)",
        "    end try",
        "    try",
        "      set winSize to size of targetWindow",
        "      set windowWidthText to my cleanText(item 1 of winSize)",
        "      set windowHeightText to my cleanText(item 2 of winSize)",
        "    end try",
        "    my appendElement(targetWindow, \"w1\", 0)",
        "  else",
        "    my appendElement(targetProcess, \"p1\", 0)",
        "  end if",
        "end tell",
        "set end of headerRows to my joinList({\"META\", \"appName\", appNameText}, tab)",
        "set end of headerRows to my joinList({\"META\", \"processId\", processIdText}, tab)",
        "set end of headerRows to my joinList({\"META\", \"title\", windowTitleText}, tab)",
        "set end of headerRows to my joinList({\"META\", \"windowX\", windowXText}, tab)",
        "set end of headerRows to my joinList({\"META\", \"windowY\", windowYText}, tab)",
        "set end of headerRows to my joinList({\"META\", \"windowWidth\", windowWidthText}, tab)",
        "set end of headerRows to my joinList({\"META\", \"windowHeight\", windowHeightText}, tab)",
        "return my joinList(headerRows & outputRows, linefeed)",
    ])


def _windows_desktop_snapshot_script(args: dict[str, Any], *, max_elements: int, max_depth: int) -> str:
    process_id_value = _coerce_positive_process_id(_process_id_arg(args))
    process_id = str(process_id_value) if process_id_value is not None else ""
    target_name = str(args.get("appName") or args.get("name") or args.get("app") or "").strip()
    return "\n".join([
        "Add-Type -AssemblyName UIAutomationClient",
        "Add-Type -AssemblyName UIAutomationTypes",
        "$sig = '[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow(); [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);'",
        "Add-Type -MemberDefinition $sig -Name Win32Snapshot -Namespace ATRIUM",
        f"$limit = {int(max_elements)}",
        f"$maxDepth = {int(max_depth)}",
        f"$targetPidText = {_ps_string(process_id)}",
        f"$targetName = {_ps_string(target_name)}",
        "$hasExplicitTarget = [bool]($targetPidText -or $targetName)",
        "$hwnd = [IntPtr]::Zero",
        "$proc = $null",
        "if ($targetPidText) {",
        "  $proc = Get-Process -Id ([int]$targetPidText) -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1",
        "  if ($proc) { $hwnd = $proc.MainWindowHandle }",
        "}",
        "if ($hwnd -eq [IntPtr]::Zero -and $targetName) {",
        "  $needle = $targetName.ToLowerInvariant()",
        "  $proc = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 -and (($_.ProcessName.ToLowerInvariant().Contains($needle)) -or ($_.MainWindowTitle.ToLowerInvariant().Contains($needle))) } | Select-Object -First 1",
        "  if ($proc) { $hwnd = $proc.MainWindowHandle }",
        "}",
        "if ($hwnd -eq [IntPtr]::Zero) {",
        "  if ($hasExplicitTarget) { Write-Error 'target application process not found'; exit 1 }",
        "  $hwnd = [ATRIUM.Win32Snapshot]::GetForegroundWindow()",
        "  $pidRef = [uint32]0",
        "  [ATRIUM.Win32Snapshot]::GetWindowThreadProcessId($hwnd, [ref]$pidRef) | Out-Null",
        "  if ($pidRef) { $proc = Get-Process -Id ([int]$pidRef) -ErrorAction SilentlyContinue }",
        "}",
        "if ($hwnd -eq [IntPtr]::Zero) { Write-Error 'foreground window not found'; exit 1 }",
        "$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)",
        "if (-not $root) { Write-Error 'UIAutomation root not found'; exit 1 }",
        "$rows = New-Object 'System.Collections.Generic.List[object]'",
        "function Add-AtriumElement($element, [string]$path, [int]$depth) {",
        "  if ($rows.Count -ge $limit -or -not $element) { return }",
        "  $current = $element.Current",
        "  $rect = $current.BoundingRectangle",
        "  $controlType = ''",
        "  try { $controlType = [string]$current.ControlType.ProgrammaticName; $controlType = $controlType -replace '^ControlType\\.', '' } catch {}",
        "  $patterns = @()",
        "  try { $patterns = @($element.GetSupportedPatterns() | ForEach-Object { [string]$_.ProgrammaticName }) } catch {}",
        "  $rows.Add([PSCustomObject]@{",
        "    path = $path",
        "    role = $controlType",
        "    name = [string]$current.Name",
        "    automationId = [string]$current.AutomationId",
        "    className = [string]$current.ClassName",
        "    enabled = [bool]$current.IsEnabled",
        "    patterns = @($patterns)",
        "    x = [int]$rect.X",
        "    y = [int]$rect.Y",
        "    width = [int]$rect.Width",
        "    height = [int]$rect.Height",
        "  }) | Out-Null",
        "  if ($depth -ge $maxDepth) { return }",
        "  try {",
        "    $children = $element.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)",
        "    for ($i = 0; $i -lt $children.Count; $i++) {",
        "      if ($rows.Count -ge $limit) { break }",
        "      Add-AtriumElement $children.Item($i) ($path + '.' + ($i + 1)) ($depth + 1)",
        "    }",
        "  } catch {}",
        "}",
        "Add-AtriumElement $root 'w1' 0",
        "$rootCurrent = $root.Current",
        "$rootRect = $rootCurrent.BoundingRectangle",
        "$out = [PSCustomObject]@{",
        "  appName = if ($proc) { $proc.ProcessName } else { $null }",
        "  processId = if ($proc) { [int]$proc.Id } else { $null }",
        "  title = [string]$rootCurrent.Name",
        "  window = [PSCustomObject]@{ x=[int]$rootRect.X; y=[int]$rootRect.Y; width=[int]$rootRect.Width; height=[int]$rootRect.Height }",
        "  elements = @($rows)",
        "}",
        "$out | ConvertTo-Json -Compress -Depth 8",
    ])


def _macos_native_desktop_snapshot(
    args: dict[str, Any],
    run_process: Callable[..., dict[str, Any]],
    *,
    max_elements: int,
    max_depth: int,
) -> dict[str, Any] | None:
    helper = _ensure_snapshot_helper()
    if helper is None:
        return None
    target_name = str(args.get("appName") or args.get("name") or args.get("app") or "").strip()
    process_id_value = _coerce_positive_process_id(_process_id_arg(args))
    process_id = str(process_id_value) if process_id_value is not None else ""
    result = run_process(
        [str(helper), process_id, target_name, str(int(max_elements)), str(int(max_depth))],
        timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 60.0)),
    )
    parsed = _json_value_from_stdout(result.get("stdout"))
    result["method"] = result.get("method") or "native_ax_snapshot"
    result["snapshotBackend"] = "native_ax"
    if isinstance(parsed, dict):
        metadata = {
            "appName": parsed.get("appName"),
            "processId": parsed.get("processId"),
            "title": parsed.get("title"),
            "window": parsed.get("window") if isinstance(parsed.get("window"), dict) else None,
        }
        raw_elements = parsed.get("elements") if isinstance(parsed.get("elements"), list) else []
        if result.get("returnCode") == 0:
            snapshot_result = _desktop_snapshot_result(
                platform=sys.platform,
                metadata=metadata,
                elements=[item for item in raw_elements if isinstance(item, dict)],
                result=result,
            )
            snapshot_result["snapshotBackend"] = "native_ax"
            snapshot_result["windowCount"] = parsed.get("windowCount")
            return snapshot_result
        result.update({
            "ok": False,
            "platform": sys.platform,
            "appName": metadata.get("appName"),
            "processId": metadata.get("processId"),
            "title": metadata.get("title"),
            "refCount": 0,
            "snapshot": {"elements": []},
        })
        if parsed.get("error") and not result.get("stderr"):
            result["stderr"] = str(parsed["error"])
        return result
    if result.get("returnCode") != 0:
        result.update({"ok": False, "platform": sys.platform, "refCount": 0, "snapshot": {"elements": []}})
        return result
    return None


def execute_desktop_snapshot(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    max_elements, max_depth = _desktop_snapshot_limits(args)
    if _is_windows():
        result = _run_windows_powershell(
            _windows_desktop_snapshot_script(args, max_elements=max_elements, max_depth=max_depth),
            run_process,
            timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 60.0)),
            sta=True,
        )
        parsed = _json_value_from_stdout(result.get("stdout"))
        if isinstance(parsed, dict):
            metadata = {
                "appName": parsed.get("appName"),
                "processId": parsed.get("processId"),
                "title": parsed.get("title"),
                "window": parsed.get("window") if isinstance(parsed.get("window"), dict) else None,
            }
            raw_elements = parsed.get("elements") if isinstance(parsed.get("elements"), list) else []
            return _desktop_snapshot_result(
                platform=sys.platform,
                metadata=metadata,
                elements=[item for item in raw_elements if isinstance(item, dict)],
                result=result,
            )
        if result.get("returnCode") == 0:
            result["returnCode"] = 1
            result["stderr"] = "desktop.snapshot did not return UIAutomation JSON metadata"
        result.update({"ok": False, "platform": sys.platform, "refCount": 0, "snapshot": {"elements": []}})
        return result
    native_result = _macos_native_desktop_snapshot(
        args,
        run_process,
        max_elements=max_elements,
        max_depth=max_depth,
    )
    if native_result is not None:
        return native_result
    result = _run_visual_command(
        run_process,
        ["osascript", "-e", _macos_desktop_snapshot_script(args, max_elements=max_elements, max_depth=max_depth)],
        timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 60.0)),
    )
    if result.get("returnCode") == 0:
        metadata, elements = _parse_macos_desktop_snapshot_stdout(result.get("stdout"))
        return _desktop_snapshot_result(platform=sys.platform, metadata=metadata, elements=elements, result=result)
    result.update({"ok": False, "platform": sys.platform, "refCount": 0, "snapshot": {"elements": []}})
    return result


def _desktop_requested_process_id(args: dict[str, Any]) -> int | None:
    return _coerce_positive_process_id(_process_id_arg(args), label="desktop.act processId")


def _desktop_state_process_id(state: dict[str, Any]) -> int | None:
    raw_pid = state.get("processId")
    if raw_pid is None or str(raw_pid).strip() == "":
        return None
    try:
        return _coerce_positive_process_id(raw_pid, label="desktop.act ref processId")
    except ValueError:
        raise ValueError("desktop.act ref processId is invalid; call desktop.snapshot again") from None


def _desktop_target_process_id(target: dict[str, Any]) -> int | None:
    raw_pid = target.get("processId")
    if raw_pid is None or str(raw_pid).strip() == "":
        return None
    try:
        return _coerce_positive_process_id(raw_pid, label="desktop.act target processId")
    except ValueError:
        raise ValueError("desktop.act target processId is invalid; call desktop.snapshot again") from None


def _desktop_ref_process_id(state: dict[str, Any], target: dict[str, Any]) -> int | None:
    state_pid = _desktop_state_process_id(state)
    return state_pid if state_pid is not None else _desktop_target_process_id(target)


def _desktop_requested_app_name(args: dict[str, Any]) -> str:
    return str(args.get("appName") or args.get("name") or args.get("app") or "").strip()


def _validate_desktop_requested_target(args: dict[str, Any], state: dict[str, Any]) -> None:
    requested_pid = _desktop_requested_process_id(args)
    if requested_pid is not None:
        state_pid = _desktop_state_process_id(state)
        if state_pid is None:
            raise ValueError("desktop.act ref process is unknown; call desktop.snapshot again")
        if state_pid != requested_pid:
            raise ValueError(
                f"desktop.act ref was captured for process {state_pid}; "
                f"call desktop.snapshot again for process {requested_pid}"
            )

    requested_app = _desktop_requested_app_name(args)
    if requested_app:
        state_app = str(state.get("appName") or "").strip()
        if not state_app:
            raise ValueError("desktop.act ref app is unknown; call desktop.snapshot again")
        if state_app.lower() != requested_app.lower():
            raise ValueError(
                f"desktop.act ref was captured for app {state_app}; "
                f"call desktop.snapshot again for app {requested_app}"
            )


def _desktop_ref_target(args: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    ref = str(args.get("ref") or "").strip()
    if not ref:
        raise ValueError("desktop.act requires ref from the latest desktop.snapshot")
    state = _read_desktop_state()
    state_platform = str(state.get("platform") or "").strip()
    if not state_platform:
        raise ValueError("desktop.act ref host is unknown; call desktop.snapshot again")
    if state_platform != sys.platform:
        raise ValueError(f"desktop.act ref was captured on {state_platform}; call desktop.snapshot again on {sys.platform}")
    _validate_desktop_requested_target(args, state)
    if not _bool_arg(args.get("allowStaleRef"), default=False):
        raw_updated_at = state.get("updatedAt")
        if raw_updated_at is None:
            raise ValueError("desktop.act ref age is unknown; call desktop.snapshot again")
        try:
            updated_at = int(float(raw_updated_at))
        except (TypeError, ValueError):
            updated_at = 0
        max_age_ms = _bounded_int(args.get("maxRefAgeMs"), default=_DESKTOP_REF_MAX_AGE_MS, minimum=1_000, maximum=60 * 60 * 1000)
        if updated_at <= 0 or int(time.time() * 1000) - updated_at > max_age_ms:
            raise ValueError("desktop.act ref is stale; call desktop.snapshot again")
    refs = state.get("refs")
    if not isinstance(refs, dict) or not isinstance(refs.get(ref), dict):
        raise ValueError("desktop.act ref not found; call desktop.snapshot first")
    target = dict(refs[ref])
    _desktop_state_process_id(state)
    _desktop_target_process_id(target)
    return ref, target, state


def _desktop_ref_center(target: dict[str, Any]) -> tuple[int, int]:
    bbox = target.get("bbox")
    if not isinstance(bbox, dict):
        raise ValueError("desktop.act ref has no actionable bounding box")
    return _center_of_bbox(bbox)


def _center_of_bbox(bbox: dict[str, Any]) -> tuple[int, int]:
    return (
        int(float(bbox["x"])) + max(1, int(float(bbox["width"]))) // 2,
        int(float(bbox["y"])) + max(1, int(float(bbox["height"]))) // 2,
    )


def _desktop_path_parts(path: Any) -> tuple[str, int, list[int]] | None:
    raw_parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    if not raw_parts:
        return None
    root = raw_parts[0].lower()
    if len(root) < 2 or root[0] not in {"w", "p"} or not root[1:].isdigit():
        return None
    root_kind = "window" if root[0] == "w" else "process"
    root_index = int(root[1:])
    if root_index <= 0:
        return None
    indexes: list[int] = []
    for raw_index in raw_parts[1:]:
        if not raw_index.isdigit():
            return None
        index = int(raw_index)
        if index <= 0:
            return None
        indexes.append(index)
    return root_kind, root_index, indexes


def _macos_desktop_native_action_script(
    args: dict[str, Any],
    target: dict[str, Any],
    state: dict[str, Any],
    action: str,
) -> str | None:
    path_parts = _desktop_path_parts(target.get("path"))
    if path_parts is None:
        return None
    root_kind, root_index, indexes = path_parts
    if action == "click":
        button = str(args.get("button") or args.get("mouseButton") or "left").strip().lower()
        if button != "left":
            return None
    elif action in {"type", "paste"}:
        pass
    else:
        return None
    process_id_value = _desktop_ref_process_id(state, target)
    process_id = str(process_id_value) if process_id_value is not None else ""
    target_name = str(state.get("appName") or target.get("appName") or "").strip()
    text_value = str(args.get("text") if args.get("text") is not None else args.get("value") or "")
    expected_role = str(target.get("role") or "").strip()
    expected_name = str(target.get("name") or "").strip()
    index_list = "{" + ", ".join(str(index) for index in indexes) + "}"
    return "\n".join([
        "on cleanIdentityText(rawValue)",
        "  try",
        "    set textValue to rawValue as text",
        "    if textValue is \"missing value\" then return \"\"",
        "    if textValue is \"button\" then return \"\"",
        "    if textValue is \"text entry area\" then return \"\"",
        "    return textValue",
        "  on error",
        "    return \"\"",
        "  end try",
        "end cleanIdentityText",
        f"set targetPid to {_applescript_string(process_id)}",
        f"set targetName to {_applescript_string(target_name)}",
        f"set rootKind to {_applescript_string(root_kind)}",
        f"set rootIndex to {int(root_index)}",
        f"set pathIndexes to {index_list}",
        f"set actionName to {_applescript_string(action)}",
        f"set textValue to {_applescript_string(text_value)}",
        f"set expectedRole to {_applescript_string(expected_role)}",
        f"set expectedName to {_applescript_string(expected_name)}",
        "set hasExplicitTarget to (targetName is not \"\" or targetPid is not \"\")",
        "tell application \"System Events\"",
        "  set targetProcess to missing value",
        "  if targetPid is not \"\" then",
        "    try",
        "      set targetProcess to first application process whose unix id is (targetPid as integer)",
        "    end try",
        "  end if",
        "  if targetProcess is missing value and targetName is not \"\" then",
        "    try",
        "      set targetProcess to first application process whose name is targetName",
        "    end try",
        "  end if",
        "  if targetProcess is missing value then",
        "    if hasExplicitTarget then error \"target application process not found\"",
        "    set targetProcess to first application process whose frontmost is true",
        "  end if",
        "  try",
        "    if rootKind is \"window\" then",
        "      set targetElement to window rootIndex of targetProcess",
        "    else",
        "      set targetElement to targetProcess",
        "    end if",
        "    repeat with childIndex in pathIndexes",
        "      set targetElement to UI element (childIndex as integer) of targetElement",
        "    end repeat",
        "    set currentRole to \"\"",
        "    set currentName to \"\"",
        "    try",
        "      set currentRole to role of targetElement as text",
        "    end try",
        "    try",
        "      set currentName to my cleanIdentityText(name of targetElement)",
        "    end try",
        "    if currentName is \"button\" or currentName is \"text entry area\" then",
        "      set currentName to \"\"",
        "    end if",
        "    if currentName is \"\" then",
        "      try",
        "        set currentName to my cleanIdentityText(title of targetElement)",
        "      end try",
        "    end if",
        "    if currentName is \"\" then",
        "      try",
        "        set currentName to my cleanIdentityText(description of targetElement)",
        "      end try",
        "    end if",
        "    if currentName is \"\" then",
        "      try",
        "        set currentName to my cleanIdentityText(value of attribute \"AXDescription\" of targetElement)",
        "      end try",
        "    end if",
        "    if currentName is \"\" then",
        "      try",
        "        set currentName to my cleanIdentityText(value of attribute \"AXTitle\" of targetElement)",
        "      end try",
        "    end if",
        "    if currentName is \"\" then",
        "      try",
        "        set currentName to my cleanIdentityText(value of targetElement)",
        "      end try",
        "    end if",
        "    if currentName is \"\" then",
        "      try",
        "        set currentName to my cleanIdentityText(value of attribute \"AXValue\" of targetElement)",
        "      end try",
        "    end if",
        "    if expectedRole is not \"\" and currentRole is not expectedRole then",
        "      return \"MISMATCH\" & tab & \"desktop.act ref role changed from \" & expectedRole & \" to \" & currentRole & \"; call desktop.snapshot again\"",
        "    end if",
        "    if expectedName is not \"\" and currentName is not expectedName then",
        "      return \"MISMATCH\" & tab & \"desktop.act ref name changed from \" & expectedName & \" to \" & currentName & \"; call desktop.snapshot again\"",
        "    end if",
        "    if actionName is \"click\" then",
        "      try",
        "        perform action \"AXPress\" of targetElement",
        "        return \"OK\" & tab & \"AXPress\"",
        "      on error pressError number pressNumber",
        "        try",
        "          click targetElement",
        "          return \"OK\" & tab & \"click\"",
        "        on error clickError number clickNumber",
        "          return \"FAIL\" & tab & pressError & \" (\" & pressNumber & \") | \" & clickError & \" (\" & clickNumber & \")\"",
        "        end try",
        "      end try",
        "    else if actionName is \"type\" or actionName is \"paste\" then",
        "      try",
        "        set value of targetElement to textValue",
        "        return \"OK\" & tab & \"setValue\"",
        "      on error valueError number valueNumber",
        "        try",
        "          set focused of targetElement to true",
        "        end try",
        "        return \"FAIL\" & tab & valueError & \" (\" & valueNumber & \")\"",
        "      end try",
        "    else",
        "      return \"UNSUPPORTED\" & tab & actionName",
        "    end if",
        "  on error errMsg number errNum",
        "    return \"FAIL\" & tab & errMsg & \" (\" & errNum & \")\"",
        "  end try",
        "end tell",
    ])


def _execute_macos_desktop_ax_action_helper(
    args: dict[str, Any],
    target: dict[str, Any],
    state: dict[str, Any],
    action: str,
    run_process: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    helper_preference = args.get("macosUseAxHelper")
    if helper_preference is None:
        helper_preference = args.get("useMacosAxHelper")
    if helper_preference is not None and not _bool_arg(helper_preference, default=True):
        return None
    path = str(target.get("path") or "").strip()
    if _desktop_path_parts(path) is None:
        return None
    if action == "click":
        button = str(args.get("button") or args.get("mouseButton") or "left").strip().lower()
        if button != "left":
            return None
    elif action in {"type", "paste", "scroll"}:
        pass
    else:
        return None
    helper = _ensure_ax_action_helper()
    if helper is None:
        return None
    process_id_value = _desktop_ref_process_id(state, target)
    process_id = str(process_id_value) if process_id_value is not None else ""
    target_name = str(state.get("appName") or target.get("appName") or "").strip()
    if action == "scroll":
        try:
            direction = _scroll_direction(args)
            unit = _scroll_unit(args)
            amount = _scroll_amount(args, unit)
        except (ValueError, TypeError) as exc:
            return {
                "returnCode": 64,
                "stdout": "",
                "stderr": str(exc),
                "method": "accessibility_ax_helper",
                "inputMethod": "accessibility",
                "nativeAction": None,
                "nativeStatus": "FAIL",
                "identityMismatch": False,
                "path": target.get("path"),
                "platform": sys.platform,
                "ok": False,
            }
        text_value = f"{direction}:{unit}:{amount}"
    else:
        text_value = str(args.get("text") if args.get("text") is not None else args.get("value") or "")
    expected_role = str(target.get("role") or "").strip()
    expected_name = str(target.get("name") or "").strip()
    result = _run_visual_command(
        run_process,
        [str(helper), process_id, target_name, path, action, text_value, expected_role, expected_name],
        timeout=max(5.0, min(float(args.get("timeoutSeconds") or 10), 30.0)),
    )
    parsed = _json_value_from_stdout(result.get("stdout"))
    parsed_dict = parsed if isinstance(parsed, dict) else {}
    result.update({
        "method": "accessibility_ax_helper",
        "inputMethod": parsed_dict.get("inputMethod") or "accessibility",
        "nativeAction": parsed_dict.get("nativeAction"),
        "nativeStatus": "OK" if parsed_dict.get("ok") is True else ("MISMATCH" if parsed_dict.get("identityMismatch") else "FAIL"),
        "identityMismatch": bool(parsed_dict.get("identityMismatch")),
        "path": target.get("path"),
        "platform": sys.platform,
        "helper": parsed_dict,
    })
    if result.get("returnCode") == 0 and parsed_dict.get("ok") is True:
        result["ok"] = True
        return result
    if result.get("returnCode") == 0:
        result["returnCode"] = 1
    if not result.get("stderr"):
        result["stderr"] = str(parsed_dict.get("error") or "macOS AX helper action failed")
    result["ok"] = False
    return result


def _execute_macos_desktop_native_action(
    args: dict[str, Any],
    target: dict[str, Any],
    state: dict[str, Any],
    action: str,
    run_process: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    helper_result = _execute_macos_desktop_ax_action_helper(args, target, state, action, run_process)
    if helper_result is not None:
        return helper_result
    script = _macos_desktop_native_action_script(args, target, state, action)
    if script is None:
        return None
    result = _run_visual_command(
        run_process,
        ["osascript", "-e", script],
        timeout=max(5.0, min(float(args.get("timeoutSeconds") or 10), 30.0)),
    )
    raw_stdout = str(result.get("stdout") or "").strip()
    parts = raw_stdout.split("\t", 1)
    status = parts[0].strip().upper() if parts and parts[0].strip() else ""
    detail = parts[1].strip() if len(parts) > 1 else raw_stdout
    result.update({
        "method": "accessibility",
        "inputMethod": "accessibility",
        "nativeAction": detail if status == "OK" else None,
        "nativeStatus": status or None,
        "identityMismatch": status == "MISMATCH",
        "path": target.get("path"),
        "platform": sys.platform,
    })
    if result.get("returnCode") == 0 and status == "OK":
        result["ok"] = True
        return result
    if result.get("returnCode") == 0:
        result["returnCode"] = 1
    if not result.get("stderr"):
        result["stderr"] = detail or raw_stdout or "macOS accessibility action failed"
    result["ok"] = False
    return result


def _windows_desktop_native_action_script(
    args: dict[str, Any],
    target: dict[str, Any],
    state: dict[str, Any],
    action: str,
) -> str | None:
    path = str(target.get("path") or "").strip()
    if _desktop_path_parts(path) is None:
        return None
    if action == "click":
        button = str(args.get("button") or args.get("mouseButton") or "left").strip().lower()
        if button != "left":
            return None
    elif action in {"type", "paste"}:
        pass
    else:
        return None
    process_id_value = _desktop_ref_process_id(state, target)
    process_id = str(process_id_value) if process_id_value is not None else ""
    target_name = str(state.get("appName") or target.get("appName") or "").strip()
    text_value = str(args.get("text") if args.get("text") is not None else args.get("value") or "")
    expected_role = str(target.get("role") or "").strip()
    expected_name = str(target.get("name") or "").strip()
    return "\n".join([
        "Add-Type -AssemblyName UIAutomationClient",
        "Add-Type -AssemblyName UIAutomationTypes",
        "$ErrorActionPreference = 'Stop'",
        "$sig = '[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow(); [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);'",
        "Add-Type -MemberDefinition $sig -Name Win32NativeAction -Namespace ATRIUM",
        f"$targetPidText = {_ps_string(process_id)}",
        f"$targetName = {_ps_string(target_name)}",
        f"$targetPath = {_ps_string(path)}",
        f"$actionName = {_ps_string(action)}",
        f"$textValue = {_ps_string(text_value)}",
        f"$expectedRole = {_ps_string(expected_role)}",
        f"$expectedName = {_ps_string(expected_name)}",
        "$hasExplicitTarget = [bool]($targetPidText -or $targetName)",
        "function Get-AtriumControlTypeName($element) {",
        "  try { return ([string]$element.Current.ControlType.ProgrammaticName) -replace '^ControlType\\.', '' } catch { return '' }",
        "}",
        "function Exit-AtriumIdentityMismatch([string]$message, [string]$currentRole, [string]$currentName) {",
        "  [PSCustomObject]@{ ok=$false; identityMismatch=$true; error=$message; path=$targetPath; action=$actionName; expectedRole=$expectedRole; expectedName=$expectedName; currentRole=$currentRole; currentName=$currentName } | ConvertTo-Json -Compress",
        "  exit 1",
        "}",
        "try {",
        "  $hwnd = [IntPtr]::Zero",
        "  $proc = $null",
        "  if ($targetPidText) {",
        "    $proc = Get-Process -Id ([int]$targetPidText) -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1",
        "    if ($proc) { $hwnd = $proc.MainWindowHandle }",
        "  }",
        "  if ($hwnd -eq [IntPtr]::Zero -and $targetName) {",
        "    $needle = $targetName.ToLowerInvariant()",
        "    $proc = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 -and (($_.ProcessName.ToLowerInvariant().Contains($needle)) -or ($_.MainWindowTitle.ToLowerInvariant().Contains($needle))) } | Select-Object -First 1",
        "    if ($proc) { $hwnd = $proc.MainWindowHandle }",
        "  }",
        "  if ($hwnd -eq [IntPtr]::Zero) {",
        "    if ($hasExplicitTarget) { throw 'target application process not found' }",
        "    $hwnd = [ATRIUM.Win32NativeAction]::GetForegroundWindow()",
        "    $pidRef = [uint32]0",
        "    [ATRIUM.Win32NativeAction]::GetWindowThreadProcessId($hwnd, [ref]$pidRef) | Out-Null",
        "    if ($pidRef) { $proc = Get-Process -Id ([int]$pidRef) -ErrorAction SilentlyContinue }",
        "  }",
        "  if ($hwnd -eq [IntPtr]::Zero) { throw 'foreground window not found' }",
        "  $root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)",
        "  if (-not $root) { throw 'UIAutomation root not found' }",
        "  $parts = $targetPath.Split('.')",
        "  if ($parts.Count -lt 1 -or $parts[0] -notmatch '^[wp][0-9]+$') { throw ('unsupported UIAutomation path: ' + $targetPath) }",
        "  $element = $root",
        "  for ($i = 1; $i -lt $parts.Count; $i++) {",
        "    $childIndex = ([int]$parts[$i]) - 1",
        "    $children = $element.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)",
        "    if ($childIndex -lt 0 -or $childIndex -ge $children.Count) { throw ('UIAutomation path segment not found: ' + $targetPath) }",
        "    $element = $children.Item($childIndex)",
        "  }",
        "  $currentRole = Get-AtriumControlTypeName $element",
        "  $currentName = [string]$element.Current.Name",
        "  if ($expectedRole -and $currentRole -ne $expectedRole) {",
        "    Exit-AtriumIdentityMismatch ('desktop.act ref role changed from ' + $expectedRole + ' to ' + $currentRole + '; call desktop.snapshot again') $currentRole $currentName",
        "  }",
        "  if ($expectedName -and $currentName -ne $expectedName) {",
        "    Exit-AtriumIdentityMismatch ('desktop.act ref name changed from ' + $expectedName + ' to ' + $currentName + '; call desktop.snapshot again') $currentRole $currentName",
        "  }",
        "  $nativeAction = ''",
        "  if ($actionName -eq 'click') {",
        "    $pattern = $null",
        "    if ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {",
        "      $pattern.Invoke()",
        "      $nativeAction = 'InvokePattern'",
        "    } elseif ($element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) {",
        "      $pattern.Toggle()",
        "      $nativeAction = 'TogglePattern'",
        "    } elseif ($element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {",
        "      $pattern.Select()",
        "      $nativeAction = 'SelectionItemPattern'",
        "    } else {",
        "      throw 'no supported UIAutomation click pattern'",
        "    }",
        "  } elseif ($actionName -eq 'type' -or $actionName -eq 'paste') {",
        "    $pattern = $null",
        "    if (-not $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {",
        "      throw 'no supported UIAutomation value pattern'",
        "    }",
        "    $pattern.SetValue($textValue)",
        "    $nativeAction = 'ValuePattern'",
        "  } else {",
        "    throw ('unsupported native action: ' + $actionName)",
        "  }",
        "  [PSCustomObject]@{ ok=$true; nativeAction=$nativeAction; inputMethod='uia'; path=$targetPath; action=$actionName; name=[string]$element.Current.Name; controlType=(Get-AtriumControlTypeName $element) } | ConvertTo-Json -Compress",
        "} catch {",
        "  [PSCustomObject]@{ ok=$false; error=[string]$_.Exception.Message; path=$targetPath; action=$actionName } | ConvertTo-Json -Compress",
        "  exit 1",
        "}",
    ])


def _execute_windows_desktop_native_action(
    args: dict[str, Any],
    target: dict[str, Any],
    state: dict[str, Any],
    action: str,
    run_process: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    script = _windows_desktop_native_action_script(args, target, state, action)
    if script is None:
        return None
    result = _run_windows_powershell(
        script,
        run_process,
        timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 45.0)),
        sta=True,
    )
    parsed = _json_value_from_stdout(result.get("stdout"))
    parsed_dict = parsed if isinstance(parsed, dict) else {}
    result.update({
        "method": "uia",
        "inputMethod": parsed_dict.get("inputMethod") or "uia",
        "nativeAction": parsed_dict.get("nativeAction"),
        "identityMismatch": bool(parsed_dict.get("identityMismatch")),
        "path": target.get("path"),
        "platform": sys.platform,
        "helper": parsed_dict,
    })
    if result.get("returnCode") == 0 and parsed_dict.get("ok") is True:
        result["ok"] = True
        return result
    if result.get("returnCode") == 0:
        result["returnCode"] = 1
    if not result.get("stderr"):
        result["stderr"] = str(parsed_dict.get("error") or "Windows UIAutomation action failed")
    result["ok"] = False
    return result


def _execute_desktop_native_action(
    args: dict[str, Any],
    target: dict[str, Any],
    state: dict[str, Any],
    action: str,
    run_process: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if not _bool_arg(args.get("preferNative"), default=True):
        return None
    if _is_windows():
        return _execute_windows_desktop_native_action(args, target, state, action, run_process)
    if sys.platform == "darwin":
        return _execute_macos_desktop_native_action(args, target, state, action, run_process)
    return None


def _desktop_action_key(action: str) -> str:
    return "double_click" if action == "double-click" else action


def _desktop_ref_supported_action_set(target: dict[str, Any], key: str) -> set[str] | None:
    raw_actions = target.get(key)
    if not isinstance(raw_actions, list):
        return None
    actions = {str(item).strip().lower().replace("-", "_") for item in raw_actions if str(item).strip()}
    return actions


_DESKTOP_NATIVE_REF_UNAVAILABLE_PATTERNS = (
    "target application process not found",
    "foreground window not found",
    "uiautomation root not found",
    "uiautomation path segment not found",
    "unsupported uiautomation path",
    "can't get ui element",
    "can’t get ui element",
    "invalid index",
)


def _desktop_native_ref_unavailable_reason(native_attempt: dict[str, Any] | None) -> str | None:
    if native_attempt is None:
        return None
    if native_attempt.get("timeout"):
        return "desktop.act native action timed out; refusing coordinate/input fallback"
    helper = native_attempt.get("helper") if isinstance(native_attempt.get("helper"), dict) else {}
    text = " ".join(
        str(value or "")
        for value in (
            native_attempt.get("stderr"),
            native_attempt.get("stdout"),
            helper.get("error"),
        )
    ).lower()
    if not any(pattern in text for pattern in _DESKTOP_NATIVE_REF_UNAVAILABLE_PATTERNS):
        return None
    detail = str(native_attempt.get("stderr") or helper.get("error") or "desktop.act native target/ref is unavailable").strip()
    return f"{detail}; refusing coordinate/input fallback"


def _desktop_ref_activation_args(state: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
    activation_args: dict[str, Any] = {}
    app_name = str(state.get("appName") or target.get("appName") or "").strip()
    if app_name:
        activation_args["appName"] = app_name
    process_id = _desktop_ref_process_id(state, target)
    if process_id is not None:
        activation_args["processId"] = process_id
    return activation_args or None


def _activate_desktop_ref_target_for_fallback(
    state: dict[str, Any],
    target: dict[str, Any],
    run_process: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    activation_args = _desktop_ref_activation_args(state, target)
    if activation_args is None:
        return None
    result = execute_activate_app(activation_args, run_process)
    result["purpose"] = "desktop.act coordinate/input fallback target activation"
    return result


def _validate_desktop_ref_action(target: dict[str, Any], action: str, *, require_native: bool) -> None:
    if target.get("enabled") is False:
        raise ValueError("desktop.act ref is disabled; choose an enabled ref from desktop.snapshot")
    action_key = _desktop_action_key(action)
    supported_actions = _desktop_ref_supported_action_set(target, "supportedActions")
    if supported_actions is not None and action_key not in supported_actions:
        supported = ", ".join(sorted(supported_actions)) or "none"
        raise ValueError(f"desktop.act action {action_key} is not supported by ref; supported actions: {supported}")
    if require_native:
        native_actions = _desktop_ref_supported_action_set(target, "nativeSupportedActions")
        if native_actions is not None and action_key not in native_actions:
            supported = ", ".join(sorted(native_actions)) or "none"
            raise ValueError(f"desktop.act native action {action_key} is not supported by ref; native supported actions: {supported}")


def execute_desktop_act(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    try:
        ref, target, state = _desktop_ref_target(args)
    except (ValueError, TypeError, KeyError) as exc:
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": str(exc),
            "platform": sys.platform,
            "action": {"ref": str(args.get("ref") or ""), "action": str(args.get("action") or "click")},
        }
    action = str(args.get("action") or "click").strip().lower()
    wait_after_ms = _bounded_int(args.get("waitAfterMs"), default=250, minimum=0, maximum=5000)
    require_native = _bool_arg(args.get("requireNative"), default=False)
    try:
        _validate_desktop_ref_action(target, action, require_native=require_native)
    except (ValueError, TypeError) as exc:
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": str(exc),
            "platform": sys.platform,
            "action": {"ref": ref, "action": action, "target": target},
        }
    action_result: dict[str, Any]
    steps: list[dict[str, Any]] = []
    native_attempt = _execute_desktop_native_action(args, target, state, action, run_process)
    if native_attempt is not None:
        steps.append(native_attempt)
    if native_attempt is not None and native_attempt.get("identityMismatch"):
        return {
            "returnCode": native_attempt.get("returnCode", 1),
            "stdout": native_attempt.get("stdout", ""),
            "stderr": native_attempt.get("stderr") or "desktop.act ref identity changed; call desktop.snapshot again",
            "ok": False,
            "platform": sys.platform,
            "action": {"ref": ref, "action": action, "target": target},
            "usedNativeAction": False,
            "nativeAttempt": native_attempt,
            "steps": steps,
        }
    ref_unavailable_reason = _desktop_native_ref_unavailable_reason(native_attempt)
    if ref_unavailable_reason:
        return {
            "returnCode": native_attempt.get("returnCode", 1) if native_attempt is not None else 1,
            "stdout": native_attempt.get("stdout", "") if native_attempt is not None else "",
            "stderr": ref_unavailable_reason,
            "ok": False,
            "platform": sys.platform,
            "action": {"ref": ref, "action": action, "target": target},
            "usedNativeAction": False,
            "nativeAttempt": native_attempt,
            "refUnavailable": True,
            "steps": steps,
        }
    if require_native and (native_attempt is None or native_attempt.get("returnCode") != 0):
        stderr = (
            native_attempt.get("stderr")
            if native_attempt is not None and native_attempt.get("stderr")
            else "desktop.act native accessibility/UIAutomation action was not available; refusing coordinate/input fallback"
        )
        return {
            "returnCode": native_attempt.get("returnCode", 1) if native_attempt is not None else 1,
            "stdout": native_attempt.get("stdout", "") if native_attempt is not None else "",
            "stderr": stderr,
            "ok": False,
            "platform": sys.platform,
            "action": {"ref": ref, "action": action, "target": target},
            "usedNativeAction": False,
            "nativeAttempt": native_attempt,
            "steps": steps,
        }
    target_activation: dict[str, Any] | None = None
    if native_attempt is None or native_attempt.get("returnCode") != 0:
        target_activation = _activate_desktop_ref_target_for_fallback(state, target, run_process)
        if target_activation is not None:
            steps.append(target_activation)
            if target_activation.get("returnCode") != 0:
                return {
                    "returnCode": target_activation.get("returnCode", 1),
                    "stdout": target_activation.get("stdout", ""),
                    "stderr": (
                        target_activation.get("stderr")
                        or "desktop.act target activation failed; refusing coordinate/input fallback"
                    ),
                    "ok": False,
                    "platform": sys.platform,
                    "action": {"ref": ref, "action": action, "target": target},
                    "usedNativeAction": False,
                    "nativeAttempt": native_attempt,
                    "targetActivation": target_activation,
                    "steps": steps,
                }
    x: int | None = None
    y: int | None = None
    if native_attempt is not None and native_attempt.get("returnCode") == 0:
        action_result = native_attempt
    elif action in {"click", "double_click", "double-click"}:
        try:
            x, y = _desktop_ref_center(target)
        except (ValueError, TypeError, KeyError) as exc:
            return {
                "returnCode": 64,
                "stdout": native_attempt.get("stdout", "") if native_attempt else "",
                "stderr": str(exc),
                "platform": sys.platform,
                "action": {"ref": ref, "action": action, "target": target},
                "nativeAttempt": native_attempt,
                "steps": steps,
            }
        button = str(args.get("button") or args.get("mouseButton") or "left").strip().lower()
        first = execute_click({"x": x, "y": y, "button": button}, run_process)
        steps.append(first)
        action_result = first
        if action in {"double_click", "double-click"} and first.get("returnCode") == 0:
            time.sleep(0.05)
            second = execute_click({"x": x, "y": y, "button": button}, run_process)
            steps.append(second)
            action_result = second
    elif action == "type":
        try:
            x, y = _desktop_ref_center(target)
        except (ValueError, TypeError, KeyError) as exc:
            return {
                "returnCode": 64,
                "stdout": native_attempt.get("stdout", "") if native_attempt else "",
                "stderr": str(exc),
                "platform": sys.platform,
                "action": {"ref": ref, "action": action, "target": target},
                "nativeAttempt": native_attempt,
                "steps": steps,
            }
        click_result = execute_click({"x": x, "y": y, "button": "left"}, run_process)
        steps.append(click_result)
        if click_result.get("returnCode") == 0:
            action_result = execute_type_text({"text": str(args.get("text") or args.get("value") or "")}, run_process)
            steps.append(action_result)
        else:
            action_result = click_result
    elif action == "paste":
        try:
            x, y = _desktop_ref_center(target)
        except (ValueError, TypeError, KeyError) as exc:
            return {
                "returnCode": 64,
                "stdout": native_attempt.get("stdout", "") if native_attempt else "",
                "stderr": str(exc),
                "platform": sys.platform,
                "action": {"ref": ref, "action": action, "target": target},
                "nativeAttempt": native_attempt,
                "steps": steps,
            }
        click_result = execute_click({"x": x, "y": y, "button": "left"}, run_process)
        steps.append(click_result)
        if click_result.get("returnCode") == 0:
            action_result = execute_paste_text({"text": str(args.get("text") or args.get("value") or "")}, run_process)
            steps.append(action_result)
        else:
            action_result = click_result
    elif action == "keypress":
        try:
            x, y = _desktop_ref_center(target)
        except (ValueError, TypeError, KeyError) as exc:
            return {
                "returnCode": 64,
                "stdout": "",
                "stderr": str(exc),
                "platform": sys.platform,
                "action": {"ref": ref, "action": action, "target": target},
                "steps": steps,
            }
        click_result = execute_click({"x": x, "y": y, "button": "left"}, run_process)
        steps.append(click_result)
        if click_result.get("returnCode") == 0:
            keys = args.get("keys")
            if not keys and args.get("key"):
                keys = [str(args["key"])]
            action_result = execute_keypress({"keys": keys}, run_process)
            steps.append(action_result)
        else:
            action_result = click_result
    elif action == "scroll":
        try:
            x, y = _desktop_ref_center(target)
        except (ValueError, TypeError, KeyError) as exc:
            return {
                "returnCode": 64,
                "stdout": "",
                "stderr": str(exc),
                "platform": sys.platform,
                "action": {"ref": ref, "action": action, "target": target},
                "steps": steps,
            }
        click_result = execute_click({"x": x, "y": y, "button": "left"}, run_process)
        steps.append(click_result)
        if click_result.get("returnCode") == 0:
            try:
                action_result = execute_scroll(
                    {
                        "direction": args.get("direction") or "down",
                        "amount": args.get("amount") or 1,
                        "unit": args.get("unit") or "page",
                        "x": x,
                        "y": y,
                    },
                    run_process,
                )
            except (ValueError, TypeError) as exc:
                action_result = {
                    "returnCode": 64,
                    "stdout": "",
                    "stderr": str(exc),
                    "platform": sys.platform,
                    "direction": args.get("direction") or "down",
                    "amount": args.get("amount") or 1,
                    "unit": args.get("unit") or "page",
                }
            steps.append(action_result)
        else:
            action_result = click_result
    else:
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": f"unsupported desktop.act action: {action}",
            "platform": sys.platform,
            "action": {"ref": ref, "action": action},
        }
    if wait_after_ms:
        time.sleep(wait_after_ms / 1000.0)
    result = {
        "returnCode": action_result.get("returnCode"),
        "stdout": action_result.get("stdout", ""),
        "stderr": action_result.get("stderr", ""),
        "ok": action_result.get("returnCode") == 0,
        "platform": sys.platform,
        "action": {"ref": ref, "action": action, "x": x, "y": y, "target": target},
        "usedNativeAction": native_attempt is not None and action_result is native_attempt and native_attempt.get("returnCode") == 0,
        "nativeAttempt": native_attempt,
        "targetActivation": target_activation,
        "steps": steps,
    }
    if _bool_arg(args.get("snapshotAfter"), default=True):
        snapshot_args = {
            "maxElements": args.get("maxElements", 120),
            "maxDepth": args.get("maxDepth", 4),
        }
        state = _read_desktop_state()
        if state.get("processId"):
            snapshot_args["processId"] = state["processId"]
        elif state.get("appName"):
            snapshot_args["appName"] = state["appName"]
        result["after"] = execute_desktop_snapshot(snapshot_args, run_process)
    return result


def _click_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_click.swift", helper_dir / "macos_click"


def _key_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_keys.swift", helper_dir / "macos_keys"


def _activate_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_activate.swift", helper_dir / "macos_activate"


def _apps_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_apps.swift", helper_dir / "macos_apps"


def _snapshot_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_snapshot.swift", helper_dir / "macos_snapshot"


def _ax_action_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_ax_action.swift", helper_dir / "macos_ax_action"


def _ensure_click_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _click_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_CLICK_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_CLICK_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def _ensure_key_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _key_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_KEY_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_KEY_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def _ensure_activate_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _activate_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_ACTIVATE_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_ACTIVATE_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def _ensure_apps_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _apps_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_APPS_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_APPS_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def _ensure_snapshot_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _snapshot_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_SNAPSHOT_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_SNAPSHOT_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def _ensure_ax_action_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _ax_action_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_AX_ACTION_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_AX_ACTION_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def execute_click(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    x = int(float(args.get("x")))
    y = int(float(args.get("y")))
    button = str(args.get("button") or args.get("mouseButton") or "left").strip().lower()
    if button not in {"left", "right"}:
        raise ValueError("click button must be left or right")

    if _is_windows():
        result = _run_windows_visual_helper("click", {"x": x, "y": y, "button": button}, run_process, timeout=8.0)
        result.update({"x": x, "y": y, "button": button, "platform": sys.platform})
        return result

    helper = _ensure_click_helper()
    if helper:
        result = run_process([str(helper), str(x), str(y), button], timeout=8.0)
        result.update({"x": x, "y": y, "button": button, "method": "coregraphics"})
        if result.get("returnCode") == 0:
            return result

    script = f'tell application "System Events" to click at {{{x}, {y}}}'
    result = run_process(["osascript", "-e", script], timeout=15.0)
    result.update({"x": x, "y": y, "button": button, "method": "osascript"})
    return result


def _normalized_key_parts(keys: Any) -> tuple[str, list[str]]:
    if not isinstance(keys, list) or not keys or not all(isinstance(key, str) for key in keys):
        raise ValueError("keypress tools require keys as a string list")
    normalized = [key.strip().lower() for key in keys if key.strip()]
    modifiers = [_MODIFIER_ALIASES[key] for key in normalized if key in _MODIFIER_ALIASES]
    key_parts = [key for key in normalized if key not in _MODIFIER_ALIASES]
    if len(key_parts) != 1:
        raise ValueError("keypress tools require exactly one non-modifier key")
    key = _KEY_ALIASES.get(key_parts[0], key_parts[0])
    return key, modifiers


def execute_keypress(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    key, modifiers = _normalized_key_parts(args.get("keys"))
    if _is_windows():
        result = _run_windows_visual_helper("keypress", {"keys": args.get("keys")}, run_process, timeout=8.0)
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        actual_key = helper.get("key") if isinstance(helper.get("key"), str) else key
        actual_modifiers = helper.get("modifiers") if isinstance(helper.get("modifiers"), list) else modifiers
        result.update({
            "key": actual_key,
            "modifiers": [str(item) for item in actual_modifiers],
            "requestedKey": key,
            "requestedModifiers": modifiers,
            "platform": sys.platform,
        })
        return result
    helper = _ensure_key_helper()
    if helper:
        result = run_process([str(helper), "press", key, *modifiers], timeout=8.0)
        result.update({"key": key, "modifiers": modifiers, "method": "coregraphics"})
        if result.get("returnCode") == 0:
            return result
    modifier_map = {"cmd": "command", "control": "control", "option": "option", "shift": "shift", "win": "command"}
    suffix = f" using {{{', '.join(f'{modifier_map[mod]} down' for mod in modifiers)}}}" if modifiers else ""
    if len(key) == 1:
        script = f'tell application "System Events" to keystroke {_applescript_string(key)}{suffix}'
    else:
        key_code = _SCROLL_KEY_CODES.get((key.replace("page", ""), "page"))
        if key == "return":
            key_code = 36
        elif key == "tab":
            key_code = 48
        elif key == "space":
            key_code = 49
        elif key == "delete":
            key_code = 51
        elif key == "forwarddelete":
            key_code = 117
        elif key == "escape":
            key_code = 53
        elif key == "insert":
            raise ValueError("unsupported key name: insert")
        elif key in {"left", "right", "down", "up"}:
            key_code = _SCROLL_KEY_CODES[(key, "line")]
        if key_code is None:
            raise ValueError(f"unsupported key name: {key}")
        script = f'tell application "System Events" to key code {key_code}{suffix}'
    result = run_process(["osascript", "-e", script], timeout=5.0)
    result.update({"key": key, "modifiers": modifiers, "method": "osascript"})
    return result


def execute_type_text(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str):
        raise ValueError("type tools require text")
    if _is_windows():
        result = _run_windows_visual_helper("type", {"text": text}, run_process, timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 60.0)))
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        result.update({
            "textBytes": len(text.encode("utf-8")),
            "textCharacters": len(text),
            "textUnits": helper.get("textUnits"),
            "platform": sys.platform,
        })
        return result
    helper = _ensure_key_helper()
    timeout = max(5.0, min(float(args.get("timeoutSeconds") or 15), 60.0))
    if helper:
        result = run_process([str(helper), "type", text], timeout=timeout)
        result.update({"textBytes": len(text.encode("utf-8")), "method": "coregraphics"})
        if result.get("returnCode") == 0:
            return result
    script = f'tell application "System Events" to keystroke {_applescript_string(text)}'
    result = run_process(["osascript", "-e", script], timeout=timeout)
    result.update({"textBytes": len(text.encode("utf-8")), "method": "osascript"})
    return result


def _macos_clipboard_verify_script(expected_text: str) -> str:
    return "\n".join([
        "on replaceText(theText, oldText, newText)",
        "  set AppleScript's text item delimiters to oldText",
        "  set theItems to text items of theText",
        "  set AppleScript's text item delimiters to newText",
        "  set theText to theItems as text",
        "  set AppleScript's text item delimiters to \"\"",
        "  return theText",
        "end replaceText",
        "",
        "on cleanText(valueText)",
        "  try",
        "    set outText to valueText as text",
        "  on error",
        "    return \"\"",
        "  end try",
        "  set outText to my replaceText(outText, tab, \" \")",
        "  set outText to my replaceText(outText, return, \" \")",
        "  set outText to my replaceText(outText, linefeed, \" \")",
        "  return outText",
        "end cleanText",
        "",
        f"set expectedText to {_applescript_string(expected_text)}",
        "try",
        "  set clipboardText to the clipboard as text",
        "on error errMsg number errNum",
        "  return \"FAIL\" & tab & errMsg & \" (\" & errNum & \")\"",
        "end try",
        "set previewText to my cleanText(clipboardText)",
        "if length of previewText is greater than 200 then set previewText to text 1 thru 200 of previewText",
        "set textLength to length of clipboardText",
        "if clipboardText is expectedText then",
        "  return \"OK\" & tab & textLength & tab & previewText",
        "end if",
        "return \"MISMATCH\" & tab & textLength & tab & previewText",
    ])


def _parse_macos_clipboard_verification(result: dict[str, Any]) -> dict[str, Any]:
    raw_stdout = str(result.get("stdout") or "").strip()
    parts = raw_stdout.split("\t", 2)
    status = parts[0].strip().upper() if parts and parts[0].strip() else ""
    text_length: int | None = None
    if len(parts) > 1:
        try:
            text_length = int(parts[1])
        except ValueError:
            text_length = None
    detail = parts[2].strip() if len(parts) > 2 else (parts[1].strip() if len(parts) > 1 else raw_stdout)
    return {
        "verifyReturnCode": result.get("returnCode"),
        "verifyStderr": result.get("stderr", ""),
        "verifyMethod": result.get("method") or "osascript",
        "verified": result.get("returnCode") == 0 and status == "OK",
        "containsExpected": result.get("returnCode") == 0 and status == "OK",
        "textLength": text_length,
        "textPreview": detail,
        "verifyStatus": status or None,
    }


def execute_paste_text(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str):
        raise ValueError("paste_text tools require text")
    if _is_windows():
        set_clipboard = _run_windows_powershell(f"Set-Clipboard -Value {_ps_string(text)}", run_process, timeout=5.0, sta=True)
        if set_clipboard.get("returnCode") != 0:
            set_clipboard.update({"textBytes": len(text.encode("utf-8")), "platform": sys.platform})
            return set_clipboard
        verify_script = "\n".join([
            "$value = Get-Clipboard -Raw -ErrorAction Stop",
            "if ($null -eq $value) { $value = '' }",
            f"$expected = {_ps_string(text)}",
            "$preview = $value",
            "if ($preview.Length -gt 200) { $preview = $preview.Substring(0, 200) }",
            "[PSCustomObject]@{ textLength=$value.Length; textPreview=$preview; containsExpected=$value.Equals($expected); verified=$value.Equals($expected) } | ConvertTo-Json -Compress",
        ])
        clipboard_check = _run_windows_powershell(verify_script, run_process, timeout=5.0, sta=True)
        clipboard_rows = _json_rows_from_stdout(clipboard_check)
        clipboard_row = clipboard_rows[0] if clipboard_rows else {}
        clipboard_meta = {
            "setReturnCode": set_clipboard.get("returnCode"),
            "setStderr": set_clipboard.get("stderr", ""),
            "setMethod": set_clipboard.get("method"),
            "verifyReturnCode": clipboard_check.get("returnCode"),
            "verifyStderr": clipboard_check.get("stderr", ""),
            "verifyMethod": clipboard_check.get("method"),
            "verified": bool(clipboard_row.get("verified")),
            "containsExpected": bool(clipboard_row.get("containsExpected")),
            "textLength": clipboard_row.get("textLength"),
            "textPreview": clipboard_row.get("textPreview"),
        }
        if clipboard_check.get("returnCode") != 0 or not clipboard_meta["verified"]:
            return {
                "command": ["Set-Clipboard", "Get-Clipboard"],
                "returnCode": clipboard_check.get("returnCode") if clipboard_check.get("returnCode") != 0 else 1,
                "stdout": clipboard_check.get("stdout", ""),
                "stderr": clipboard_check.get("stderr") or "clipboard round-trip did not verify expected text",
                "textBytes": len(text.encode("utf-8")),
                "method": clipboard_check.get("method"),
                "clipboard": clipboard_meta,
                "platform": sys.platform,
            }
        paste = execute_keypress({"keys": ["control", "v"]}, run_process)
        return {
            "command": ["Set-Clipboard", paste.get("method", "keypress"), "paste"],
            "returnCode": paste.get("returnCode"),
            "stdout": paste.get("stdout", ""),
            "stderr": paste.get("stderr", ""),
            "textBytes": len(text.encode("utf-8")),
            "method": paste.get("method"),
            "inputMethod": paste.get("inputMethod"),
            "ok": paste.get("ok"),
            "helper": paste.get("helper"),
            "helperMode": paste.get("helperMode"),
            "clipboard": clipboard_meta,
            "platform": sys.platform,
        }
    set_clipboard = _run_visual_command(
        run_process,
        ["osascript", "-e", f"set the clipboard to {_applescript_string(text)}"],
        timeout=5.0,
    )
    set_clipboard["method"] = set_clipboard.get("method") or "osascript"
    if set_clipboard.get("returnCode") != 0:
        return {
            "command": ["osascript", "set clipboard"],
            "returnCode": set_clipboard.get("returnCode"),
            "stdout": set_clipboard.get("stdout", ""),
            "stderr": set_clipboard.get("stderr", ""),
            "textBytes": len(text.encode("utf-8")),
            "method": set_clipboard.get("method"),
            "clipboard": {
                "setReturnCode": set_clipboard.get("returnCode"),
                "setStderr": set_clipboard.get("stderr", ""),
                "setMethod": set_clipboard.get("method"),
                "verified": False,
                "containsExpected": False,
            },
            "platform": sys.platform,
        }
    verify_result = _run_visual_command(
        run_process,
        ["osascript", "-e", _macos_clipboard_verify_script(text)],
        timeout=5.0,
    )
    verify_result["method"] = verify_result.get("method") or "osascript"
    clipboard_meta = {
        "setReturnCode": set_clipboard.get("returnCode"),
        "setStderr": set_clipboard.get("stderr", ""),
        "setMethod": set_clipboard.get("method"),
        **_parse_macos_clipboard_verification(verify_result),
    }
    if verify_result.get("returnCode") != 0 or not clipboard_meta["verified"]:
        return {
            "command": ["osascript", "set clipboard", "verify clipboard"],
            "returnCode": verify_result.get("returnCode") if verify_result.get("returnCode") != 0 else 1,
            "stdout": verify_result.get("stdout", ""),
            "stderr": verify_result.get("stderr") or "clipboard round-trip did not verify expected text",
            "textBytes": len(text.encode("utf-8")),
            "method": verify_result.get("method"),
            "clipboard": clipboard_meta,
            "platform": sys.platform,
        }
    paste = execute_keypress({"keys": ["cmd", "v"]}, run_process)
    return {
        "command": ["osascript", "set clipboard", paste.get("method", "keypress"), "paste"],
        "returnCode": paste.get("returnCode"),
        "stdout": paste.get("stdout", ""),
        "stderr": paste.get("stderr", ""),
        "textBytes": len(text.encode("utf-8")),
        "method": paste.get("method"),
        "inputMethod": paste.get("inputMethod") or paste.get("method"),
        "ok": paste.get("ok"),
        "helper": paste.get("helper"),
        "helperMode": paste.get("helperMode"),
        "clipboard": clipboard_meta,
        "platform": sys.platform,
    }


def visual_process_error(tool: str, result: Any) -> str | None:
    if tool not in _VISUAL_PROCESS_TOOLS or not isinstance(result, dict):
        return None
    return_code = result.get("returnCode")
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    if result.get("timeout") is True:
        detail = stderr or stdout or "command timed out"
        return f"{tool} bridge command failed: {detail[:1000]}"
    if result.get("ok") is False:
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        detail = stderr or str(helper.get("error") or helper.get("message") or "") or stdout or "ok=false"
        return f"{tool} bridge command failed: {detail[:1000]}"
    if return_code is None:
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        detail = stderr or str(result.get("error") or helper.get("error") or helper.get("message") or "") or stdout
        return f"{tool} bridge command failed: {detail[:1000]}" if detail else None
    if return_code == 0:
        return None
    detail = stderr or stdout or f"returnCode={return_code}"
    return f"{tool} bridge command failed: {detail[:1000]}"


def _scroll_direction(args: dict[str, Any]) -> str:
    raw = str(args.get("direction") or "").strip().lower()
    if not raw:
        delta_y = args.get("deltaY") if "deltaY" in args else args.get("dy")
        delta_x = args.get("deltaX") if "deltaX" in args else args.get("dx")
        try:
            if delta_y is not None and float(delta_y) != 0:
                raw = "down" if float(delta_y) > 0 else "up"
            elif delta_x is not None and float(delta_x) != 0:
                raw = "right" if float(delta_x) > 0 else "left"
        except (TypeError, ValueError):
            raw = ""
    aliases = {
        "d": "down",
        "u": "up",
        "r": "right",
        "l": "left",
        "pagedown": "down",
        "page_down": "down",
        "pageup": "up",
        "page_up": "up",
    }
    direction = aliases.get(raw, raw or "down")
    if direction not in {"down", "up", "left", "right"}:
        raise ValueError("scroll direction must be one of down, up, left, or right")
    return direction


def _scroll_unit(args: dict[str, Any]) -> str:
    raw = str(args.get("unit") or "").strip().lower()
    if raw in {"line", "lines", "row", "rows"}:
        return "line"
    if raw in {"page", "pages", ""}:
        return "page"
    if raw in {"pixel", "pixels", "px"}:
        return "page"
    raise ValueError("scroll unit must be page or line")


def _scroll_amount(args: dict[str, Any], unit: str) -> int:
    raw = args.get("amount")
    if raw is None and unit == "page":
        raw = args.get("pages")
    if raw is None and unit == "line":
        raw = args.get("lines")
    if raw is None:
        raw = 1
    try:
        amount = int(abs(float(raw)))
    except (TypeError, ValueError):
        amount = 1
    return max(1, min(amount, 10 if unit == "page" else 40))


def execute_scroll(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    direction = _scroll_direction(args)
    unit = _scroll_unit(args)
    amount = _scroll_amount(args, unit)
    delay_ms = max(0, min(int(args.get("delayMs") or 40), 500))
    if _is_windows():
        payload: dict[str, Any] = {"direction": direction, "unit": unit, "amount": amount, "delayMs": delay_ms}
        x = y = None
        if args.get("x") is not None or args.get("y") is not None:
            if args.get("x") is None or args.get("y") is None:
                raise ValueError("scroll x and y must be provided together")
            x = int(float(args["x"]))
            y = int(float(args["y"]))
            payload.update({"x": x, "y": y})
        result = _run_windows_visual_helper(
            "scroll",
            payload,
            run_process,
            timeout=max(5.0, min(5.0 + amount * 0.4, 30.0)),
        )
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        result.update({
            "direction": direction,
            "unit": unit,
            "amount": amount,
            "steps": helper.get("steps"),
            "wheelDelta": helper.get("wheelDelta"),
            "horizontal": helper.get("horizontal"),
            "x": helper.get("x") if helper.get("x") is not None else x,
            "y": helper.get("y") if helper.get("y") is not None else y,
            "platform": sys.platform,
        })
        return result
    key_code = _SCROLL_KEY_CODES[(direction, unit)]
    delay_s = delay_ms / 1000
    script = "\n".join(
        [
            'tell application "System Events"',
            f"  repeat {amount} times",
            f"    key code {key_code}",
            f"    delay {delay_s:.3f}",
            "  end repeat",
            "end tell",
        ]
    )
    result = run_process(["osascript", "-e", script], timeout=max(5.0, min(5.0 + amount * 0.2, 20.0)))
    result.update({
        "direction": direction,
        "unit": unit,
        "amount": amount,
        "keyCode": key_code,
    })
    return result


def execute_notification(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    title = str(args.get("title") or "ATRIUM")
    body = str(args.get("body") or "")
    if _is_windows():
        timeout_ms = max(1000, min(int(args.get("timeoutMs") or args.get("durationMs") or 5000), 10000))
        script = "\n".join([
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$shown = $false",
            "$disposed = $false",
            "$notify = New-Object System.Windows.Forms.NotifyIcon",
            "try {",
            "  $notify.Icon = [System.Drawing.SystemIcons]::Information",
            "  $notify.Visible = $true",
            f"  $notify.ShowBalloonTip({timeout_ms}, {_ps_string(title)}, {_ps_string(body)}, [System.Windows.Forms.ToolTipIcon]::Info)",
            "  $shown = $true",
            f"  Start-Sleep -Milliseconds {min(timeout_ms + 250, 11000)}",
            "} finally {",
            "  if ($notify) { $notify.Dispose(); $disposed = $true }",
            "}",
            f"[PSCustomObject]@{{ shown=$shown; disposed=$disposed; timeoutMs={timeout_ms}; titleLength={len(title)}; bodyLength={len(body)} }} | ConvertTo-Json -Compress",
        ])
        command_timeout = max(10.0, (timeout_ms + 1500) / 1000.0)
        result = _run_windows_powershell(script, run_process, timeout=command_timeout, sta=True)
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        result.update({
            "title": title,
            "bodyBytes": len(body.encode("utf-8")),
            "shown": row.get("shown"),
            "disposed": row.get("disposed"),
            "timeoutMs": row.get("timeoutMs"),
            "titleLength": row.get("titleLength"),
            "bodyLength": row.get("bodyLength"),
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0 and row.get("shown") is not True:
            result["returnCode"] = 1
            result["stderr"] = "Windows notification did not return ShowBalloonTip verification metadata"
        return result
    safe_title = title.replace('"', "'")
    safe_body = body.replace('"', "'")
    result = run_process(["osascript", "-e", f'display notification "{safe_body}" with title "{safe_title}"'], timeout=5.0)
    result.update({"title": title, "bodyBytes": len(body.encode("utf-8")), "platform": sys.platform})
    return result


async def persist_screenshot_artifact(
    repo: Any,
    *,
    path: Path,
    owner_dept: str,
    created_by: str,
    source_tool: str,
    artifact_name: str | None = None,
    browser_profile: str | None = None,
) -> dict[str, Any]:
    now = now_ms()
    data = path.read_bytes()
    width, height = _png_dimensions(data)
    artifact_id = uid("art")
    name = safe_filename(artifact_name or path.name or f"{source_tool.replace('.', '-')}-{int(time.time())}.png")
    stored_path = (get_settings().workspace_dir / owner_dept / "artifacts" / artifact_id / "v1.png").resolve()
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    if stored_path != path.resolve():
        stored_path.write_bytes(data)
    path = stored_path
    tags = ["screenshot", source_tool.replace(".", "_")]
    preview = {"kind": "screenshot", "uri": str(path)}
    artifact = Artifact(
        id=artifact_id,
        name=name,
        kind="image",
        mime="image/png",
        owner_dept=owner_dept,
        task_ids=[],
        project_id=None,
        version=1,
        status="approved",
        uri=str(path),
        storage="filesystem",
        content_hash=hashlib.sha256(data).hexdigest(),
        content_size_bytes=len(data),
        content_mime="image/png",
        tags=tags,
        links=[str(path)],
        preview=preview,
        created_at=now,
        created_by=created_by,
        updated_at=now,
        updated_by=created_by,
    ).dump()
    artifact["visualAutomation"] = {
        "sourceTool": source_tool,
        "coordinateSpace": "screen_pixels",
        "width": width,
        "height": height,
    }
    if browser_profile:
        artifact["visualAutomation"]["browserProfile"] = normalize_browser_profile(browser_profile)
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=1,
        author=created_by,
        ts=now,
        note=f"captured by {source_tool}",
        uri=str(path),
        storage="filesystem",
        content_hash=artifact["contentHash"],
        content_size_bytes=len(data),
        content_mime="image/png",
        preview=preview,
    ).dump()
    version["visualAutomation"] = artifact["visualAutomation"]
    await repo.put_entity("artifact", artifact, dept=owner_dept, project=None, status="approved", ts=now)
    await repo.put_entity(
        "artifact_version",
        {**version, "id": f"{artifact_id}:1"},
        dept=owner_dept,
        project=None,
        status="approved",
        ts=now,
    )
    return artifact
