#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
chaoxing-quiz — 学习通章节测验自动提交
=====================================
只做「章节测验」，跳过作业和考试。
随机作答直接提交，不追求正确率，目标是把任务点从 1 变成 0。

用法:
    python quiz.py                # 跑全部待做章节测验
    python quiz.py --limit 5      # 只跑前 5 节（试水）
    python quiz.py --dry          # 只扫描不提交
    python quiz.py --login        # 只做登录并保存登录态
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
PROFILE_DIR = BASE / "profile"
STATE_PATH = BASE / "state.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

STRONG_AUTH = {"_uid", "UID", "uf", "_d"}

START_TS = datetime.now()
LOG_FILE = LOG_DIR / f"quiz_{START_TS.strftime('%Y%m%d_%H%M%S')}.log"


def log(msg, level="INFO"):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {level:<5} {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def rnd(rng):
    return random.uniform(float(rng[0]), float(rng[1]))


def load_cfg():
    if not CFG_PATH.exists():
        log(f"配置文件不存在: {CFG_PATH}", "FATAL")
        sys.exit(2)
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    url = cfg.get("course_url", "")
    if "〔" in url:
        log("course_url 还有占位符", "FATAL")
        sys.exit(2)
    return cfg


# ---------- 浏览器 ----------

def launch(pw, cfg):
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check", "--no-first-run", "--lang=zh-CN",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
        "--disable-background-media-suspend",
        "--disable-ipc-flooding-protection",
    ]
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=bool(cfg.get("headless", False)),
        viewport={"width": 1440, "height": 900},
        args=args,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
        window.chrome = {runtime: {}};
        try {
            Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
            Object.defineProperty(document, 'visibilityState',
                {get: () => 'visible', configurable: true});
        } catch(e) {}
    """)

    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            clean = []
            for c in st.get("cookies", []):
                ck = {"name": c["name"], "value": c["value"],
                      "domain": c.get("domain", ".chaoxing.com"),
                      "path": c.get("path", "/")}
                ss = c.get("sameSite")
                if ss in ("Strict", "Lax", "None"):
                    ck["sameSite"] = ss
                if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
                    ck["expires"] = float(c["expires"])
                if c.get("httpOnly") is not None:
                    ck["httpOnly"] = bool(c["httpOnly"])
                if c.get("secure") is not None:
                    ck["secure"] = bool(c["secure"])
                clean.append(ck)
            if clean:
                ctx.add_cookies(clean)
                log(f"已注入 {len(clean)} 个 cookie")
        except Exception as e:
            log(f"注入 cookie 失败: {e}", "WARN")
    else:
        log("未找到 state.json，先跑 python quiz.py --login", "WARN")

    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    if cfg.get("minimize_on_start"):
        try:
            time.sleep(1.5)
            cdp = ctx.new_cdp_session(page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds",
                     {"windowId": wid, "bounds": {"windowState": "minimized"}})
            log("窗口已最小化")
        except Exception as e:
            log(f"最小化失败: {e}", "WARN")

    return ctx, page


# ---------- 登录 ----------

def is_logged_in(page):
    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if st.get("_auth_keys"):
                live = {c["name"] for c in page.context.cookies()}
                if live & set(st["_auth_keys"]):
                    return True
        except Exception:
            pass
    return bool({c["name"] for c in page.context.cookies()} & STRONG_AUTH)


def do_login(ctx, page):
    if is_logged_in(page):
        log(f"已有登录态: {sorted({c['name'] for c in ctx.cookies()} & STRONG_AUTH)}")
    else:
        page.goto("https://passport2.chaoxing.com/login",
                  wait_until="domcontentloaded", timeout=60000)
        log("=" * 56)
        log("请扫码登录（手机上务必点「确认登录」）")
        log("=" * 56)
        deadline = time.time() + 480
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            if {c["name"] for c in ctx.cookies()} & STRONG_AUTH:
                page.wait_for_timeout(2500)
                if is_logged_in(page):
                    log("登录成功 ✓")
                    break
            left = int(deadline - time.time())
            if left % 30 < 3:
                log(f"   等待中... 剩余 {left}s")
        else:
            log("超时", "FATAL")
            return False

    for url in ("https://i.chaoxing.com/base",
                "https://mooc1.chaoxing.com/visit/courses"):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
        except Exception:
            pass

    ctx.storage_state(path=str(STATE_PATH))
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    data["_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["_auth_keys"] = sorted({c["name"] for c in data.get("cookies", [])
                                 if c["name"] in STRONG_AUTH})
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✓ 登录态已保存: {len(data.get('cookies', []))} 个 cookie")
    return True


# ---------- 目录 / 章节 ----------

def scan_chapters(page):
    """返回 [{no, text, count}]，按章节号排序。"""
    data = page.evaluate("""() => {
        const root = document.querySelector('.right') || document;
        const rows = [];
        root.querySelectorAll('a').forEach(a => {
            const t = (a.innerText || '').trim();
            const m = t.match(/^(\\d+[.\\-]\\d+)\\s*/);
            if (!m) return;
            const rc = a.getBoundingClientRect();
            if (rc.width <= 0 || rc.height <= 0) return;

            let count = null;
            let box = a;
            for (let k = 0; k < 4 && box; k++, box = box.parentElement) {
                const badge = box.querySelector('.jobCount, .roundpointStudent');
                if (badge) {
                    const txt = (badge.innerText || '').trim();
                    const isDone = /icon_ok|finish|complete|\\bok\\b/i.test(
                        (badge.className || '') + ' ' + (badge.innerHTML || ''));
                    count = /^\\d+$/.test(txt) ? parseInt(txt, 10) : (isDone ? 0 : 0);
                    break;
                }
            }
            if (count === null) count = 0;
            rows.push({no: m[1], text: t.slice(0, 50), count: count});
        });
        return rows;
    }""")

    def key(r):
        a, b = r["no"].split(".")
        return (int(a), int(b))

    data.sort(key=key)
    return data


def click_chapter(page, no):
    return page.evaluate("""(no) => {
        const root = document.querySelector('.right') || document;
        for (const a of root.querySelectorAll('a')) {
            const t = (a.innerText || '').trim();
            if (t.startsWith(no + ' ') || t.startsWith(no + '\\u3000')) {
                a.scrollIntoView({block: 'center'});
                a.click();
                return true;
            }
        }
        return false;
    }""", no)


def find_quiz_frame(page, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for f in page.frames:
            if "doHomeWorkNew" in f.url:
                return f
        page.wait_for_timeout(1000)
    return None


# ---------- 作答 ----------

def pick_options_real(frame, page):
    """用真实鼠标点击选中答案。

    关键教训（实测）：
      超星的 ul 带 aria-hidden="true"，JS 的 el.click() 被可访问性层挡住，
      完全不生效（checkbox 仍是 false，hidden input 仍是空）。
      只有 Playwright 的真实鼠标点击才能触发页面的 addcheck() 逻辑。

    备用路径：直接调页面上的 addcheck(qid) 业务函数。
    """
    plan = frame.evaluate("""() => {
        const items = [...document.querySelectorAll('.TiMu')];
        return items.map((t, i) => {
            const allLis = [...t.querySelectorAll('li')];
            // 题型判定看 li[role]，不要依赖 li 内部有没有 input
            const checkLis = allLis.filter(li => {
                const role = (li.getAttribute('role') || '').toLowerCase();
                if (role === 'checkbox') return true;
                if (/before-after-checkbox/.test((li.className || '').toString())) return true;
                if (/addcheck/i.test(li.getAttribute('onclick') || '')) return true;
                return false;
            });
            const radioLis = allLis.filter(li => {
                const role = (li.getAttribute('role') || '').toLowerCase();
                if (role === 'radio') return true;
                const cls = (li.className || '').toString();
                if (/before-after/.test(cls) && !/checkbox/.test(cls)) return true;
                return false;
            });
            const texts = [...t.querySelectorAll('input[type=text]')].filter(x => !x.disabled);
            const areas = [...t.querySelectorAll('textarea')].filter(x => !x.disabled);

            let type = 'none', idxs = [];
            const qid = t.getAttribute('data') || '';
            if (checkLis.length) {
                type = 'multi';
                const n = Math.max(2, Math.ceil(checkLis.length / 2));
                idxs = checkLis.slice().sort(() => Math.random() - 0.5).slice(0, n)
                              .map(li => allLis.indexOf(li));
            } else if (radioLis.length) {
                type = 'single';
                const p = radioLis[Math.floor(Math.random() * radioLis.length)];
                idxs = [allLis.indexOf(p)];
            } else if (texts.length) {
                type = 'fill';
            } else if (areas.length) {
                type = 'essay';
            }
            return {q: i + 1, type, idxs, qid};
        });
    }""")

    stats = {"total": len(plan), "single": 0, "multi": 0, "other": 0,
             "filled": 0, "failed": [], "detail": []}

    for item in plan:
        q, t = item["q"], item["type"]
        if t == "single":
            stats["single"] += 1
        elif t == "multi":
            stats["multi"] += 1
        else:
            stats["other"] += 1

        if t in ("single", "multi") and item["idxs"]:
            ok_any = False
            for li_idx in item["idxs"]:
                clicked = False
                # 方式 A：Playwright 真实点击
                try:
                    loc = frame.locator(".TiMu").nth(q - 1).locator("li").nth(li_idx)
                    loc.scroll_into_view_if_needed(timeout=3000)
                    page.wait_for_timeout(120)
                    loc.click(timeout=4000, force=True)
                    clicked = True
                except Exception:
                    clicked = False

                # 方式 B：调 addcheck 兜底
                if not clicked:
                    try:
                        frame.evaluate("""({qid, liIdx}) => {
                            const t = document.querySelector('.TiMu[data="' + qid + '"]');
                            if (!t) return;
                            const li = t.querySelectorAll('li')[liIdx];
                            if (!li) return;
                            const m = (li.getAttribute('onclick') || '')
                                        .match(/addcheck\\((\\d+)\\)/i);
                            if (m && typeof addcheck === 'function') addcheck(m[1]);
                            else li.click();
                        }""", {"qid": item["qid"], "liIdx": li_idx})
                        clicked = True
                    except Exception:
                        pass

                if clicked:
                    ok_any = True
                page.wait_for_timeout(random.randint(150, 400))

            if ok_any:
                stats["filled"] += 1
                stats["detail"].append({"q": q, "type": t, "picked": len(item["idxs"])})
            else:
                stats["failed"].append(q)

        elif t in ("fill", "essay"):
            try:
                frame.evaluate("""({qid, type}) => {
                    const t = document.querySelector('.TiMu[data="' + qid + '"]');
                    if (!t) return;
                    const els = type === 'fill'
                        ? [...t.querySelectorAll('input[type=text]')]
                        : [...t.querySelectorAll('textarea')];
                    els.forEach(x => {
                        x.focus();
                        x.value = type === 'fill' ? '正确' : '根据课程内容作答。';
                        x.dispatchEvent(new Event('input', {bubbles: true}));
                        x.dispatchEvent(new Event('change', {bubbles: true}));
                        x.blur();
                    });
                }""", {"qid": item["qid"], "type": t})
                stats["filled"] += 1
                stats["detail"].append({"q": q, "type": t})
            except Exception:
                stats["failed"].append(q)
        else:
            stats["failed"].append(q)

    return stats


def verify_answered(frame):
    """检查每题是否真的被选中。

    多选题的选中态可能只在 li 的 class / aria-checked 上，
    也可能只同步到 hidden input，三种都要查。
    """
    return frame.evaluate("""() => {
        const items = [...document.querySelectorAll('.TiMu')];
        const res = {total: items.length, checked: 0, empty: []};
        items.forEach((t, i) => {
            const inputChecked = !!t.querySelector(
                'input[type=radio]:checked, input[type=checkbox]:checked');
            const hasText = [...t.querySelectorAll('input[type=text], textarea')]
                .some(x => (x.value || '').trim().length > 0);
            const liActive = [...t.querySelectorAll('li')].some(li => {
                const c = (li.className || '').toString();
                const aria = (li.getAttribute('aria-checked') || '').toLowerCase();
                return aria === 'true' ||
                       /check_answer|checkans|cur|active|selected|checked|on\\b/i.test(c);
            });
            if (inputChecked || hasText || liActive) res.checked++;
            else res.empty.push(i + 1);
        });
        return res;
    }""")


def submit_quiz(frame):
    """点提交 + 确认。返回 'ok' / 'need_retry' / 'fail:<原因>'"""
    chk = verify_answered(frame)
    log(f"  作答检查: {chk['checked']}/{chk['total']} 题已选")
    if chk["empty"]:
        log(f"  未作答的题: {chk['empty']}", "WARN")
        if len(chk["empty"]) == chk["total"]:
            return "fail:no-answers"

    clicked = frame.evaluate("""() => {
        const cands = [...document.querySelectorAll('a, button, input[type=button], input[type=submit]')];
        for (const el of cands) {
            const t = ((el.innerText || el.value || '') + '').trim();
            if (t !== '提交' && t !== '交卷' && t !== '提交答案') continue;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            el.click();
            return {ok: true, cls: (el.className || '').toString()};
        }
        return {ok: false};
    }""")
    if not clicked.get("ok"):
        return "fail:no-submit-button"
    log(f"  已点提交 ({clicked.get('cls')})")
    time.sleep(2)

    # 确认弹窗
    confirmed = False
    deadline = time.time() + 15
    while time.time() < deadline:
        r = frame.evaluate("""() => {
            const wins = [...document.querySelectorAll(
                '#confirmSubWin, .AlertCon02, .layui-layer, .dialog')];
            for (const win of wins) {
                const rect = win.getBoundingClientRect();
                const cs = getComputedStyle(win);
                if (rect.height <= 0 || cs.display === 'none' || cs.visibility === 'hidden') continue;
                const txt = (win.innerText || '');
                if (!/确认提交|确定提交|是否提交/.test(txt)) continue;
                for (const b of win.querySelectorAll('a, button, input, span')) {
                    const t = ((b.innerText || b.value || '') + '').trim();
                    if (t === '确定' || t === '确认') {
                        b.click();
                        return {done: true};
                    }
                }
                return {found: true};
            }
            return {done: false};
        }""")
        if r.get("done"):
            log("  已确认弹窗")
            confirmed = True
            break
        time.sleep(1)

    if not confirmed:
        log("  没有确认弹窗（可能直接提交）", "WARN")

    # 等结果
    deadline = time.time() + 30
    while time.time() < deadline:
        r = frame.evaluate("""() => {
            const txt = (document.body.innerText || '').slice(0, 5000);
            const codeWin = document.querySelector('#verifyCodeWin');
            const codeVisible = codeWin && getComputedStyle(codeWin).display !== 'none';
            return {
                needRetry: /未达到及格线|请重做|再做一次/.test(txt),
                needCode: /验证码/.test(txt) && codeVisible,
                submitted: /已提交|提交成功|已完成|查看答案|你的得分|本次得分/.test(txt),
                stillPending: /待完成/.test(txt),
            };
        }""")
        if r.get("needRetry"):
            return "need_retry"
        if r.get("needCode"):
            return "fail:verify-code"
        if r.get("submitted") and not r.get("stillPending"):
            return "ok"
        time.sleep(2)

    final = frame.evaluate("""() => {
        const txt = (document.body.innerText || '').slice(0, 5000);
        return {stillPending: /待完成/.test(txt), wancheng: /已完成/.test(txt)};
    }""")
    if final.get("wancheng") and not final.get("stillPending"):
        return "ok"
    return "fail:unconfirmed"


# ---------- 单节处理 ----------

def process_chapter(page, no, do_submit=True):
    log(f"--- 章节 {no} ---")

    if not click_chapter(page, no):
        return "fail:click-chapter"
    page.wait_for_timeout(int(rnd([3, 5]) * 1000))

    # 点「章节测验」标签
    tab_ok = False
    for sel in ["#dct2", "text=章节测验"]:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                tab_ok = True
                break
        except Exception:
            continue
    if not tab_ok:
        return "skip:no-quiz-tab"

    page.wait_for_timeout(int(rnd([5, 8]) * 1000))

    qf = find_quiz_frame(page)
    if qf is None:
        return "skip:no-quiz-frame"

    # 排除考试/作业
    meta = qf.evaluate("""() => {
        const txt = (document.body.innerText || '').slice(0, 3000);
        return {
            isExam: /考试/.test(txt) || /exam/i.test(location.href),
            hasTimer: !!document.querySelector('.examTime, #examTime, .countdown, .timer'),
        };
    }""")
    if meta.get("hasTimer") or meta.get("isExam"):
        log("  检测到考试/计时，跳过", "WARN")
        return "skip:exam"

    done_flag = qf.evaluate("""() => {
        const txt = (document.body.innerText || '').slice(0, 2000);
        return /已完成|已提交|查看答案|重做/.test(txt);
    }""")
    if done_flag:
        log("  已完成，跳过")
        return "skip:already-done"

    if not do_submit:
        log("  dry 模式，不提交")
        return "dry"

    stat = pick_options_real(qf, page)
    log(f"  题目: 共{stat['total']} 单选{stat['single']} 多选{stat['multi']} "
        f"其他{stat['other']} 已填{stat['filled']}")
    for d in stat.get("detail", []):
        log(f"    Q{d['q']} [{d['type']}] picked={d.get('picked','-')}")
    if stat.get("failed"):
        log(f"  未能作答: {stat['failed']}", "WARN")

    page.wait_for_timeout(int(rnd([1, 3]) * 1000))

    r = submit_quiz(qf)
    log(f"  提交结果: {r}")

    if r == "need_retry":
        log("  未及格，重做一遍", "WARN")
        page.wait_for_timeout(int(rnd([2, 4]) * 1000))
        stat2 = pick_options_real(qf, page)
        log(f"  重做: 已填 {stat2['filled']}/{stat2['total']}")
        page.wait_for_timeout(1500)
        r2 = submit_quiz(qf)
        log(f"  二次提交: {r2}")
        return r2

    return r


def main():
    ap = argparse.ArgumentParser(description="学习通章节测验自动提交")
    ap.add_argument("--login", action="store_true", help="只做登录并保存登录态")
    ap.add_argument("--dry", action="store_true", help="只扫描不提交")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 节")
    args = ap.parse_args()

    cfg = load_cfg()

    with sync_playwright() as pw:
        ctx, page = launch(pw, cfg)

        if args.login:
            do_login(ctx, page)
            ctx.close()
            return

        if not is_logged_in(page):
            log("未登录，先跑 python quiz.py --login", "FATAL")
            ctx.close()
            return

        page.goto(cfg["course_url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(int(rnd([6, 10]) * 1000))

        chs = scan_chapters(page)
        todo = [c for c in chs if c["count"] >= 1]
        log("=" * 56)
        log(f"总章节 {len(chs)}，待做测验 {len(todo)}")
        log(f"模式: {'扫描不提交' if args.dry else '自动作答并提交'}")
        log("=" * 56)

        if args.limit:
            todo = todo[:args.limit]
            log(f"限制只跑 {len(todo)} 节")

        stats = {"ok": 0, "retry": 0, "skip": 0, "fail": 0}
        for i, ch in enumerate(todo, 1):
            log(f"\n===== [{i}/{len(todo)}] {ch['no']} {ch['text'][:30]} "
                f"(jobCount={ch['count']}) =====")
            try:
                r = process_chapter(page, ch["no"], do_submit=not args.dry)
            except Exception as e:
                log(f"处理异常: {str(e)[:120]}", "WARN")
                r = "fail:exception"

            if r.startswith("ok"):
                stats["ok"] += 1
            elif r == "need_retry":
                stats["retry"] += 1
            elif r.startswith("skip"):
                stats["skip"] += 1
            elif r == "dry":
                pass
            else:
                stats["fail"] += 1
                log(f"  失败: {r}", "WARN")

            page.wait_for_timeout(int(rnd([2, 5]) * 1000))

        log("\n" + "=" * 56)
        log(f"完成: 成功{stats['ok']} 重做{stats['retry']} "
            f"跳过{stats['skip']} 失败{stats['fail']}")
        log(f"日志: {LOG_FILE}")
        log("=" * 56)

        page.wait_for_timeout(3000)
        ctx.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("手动中断", "WARN")
