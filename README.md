# chaoxing-quiz

> 学习通（超星）章节测验自动提交 — 只做章节测验，跳过作业和考试

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-green)](https://playwright.dev/python/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 这是什么

配合 [chaoxing-video](../chaoxing-video) 的第二个环节。

刷课项目把视频任务点从 `2` 降到 `1`，剩下的那个 `1` 就是**章节测验**。这个项目负责把 `1` 变成 `0`。

设计原则：

> **不追求正确率，追求「做完」。**

随机选答案直接提交，只为把任务点清掉。**每题都真实作答，只是答案随机。** 如果你在意分数，跑完之后自己看哪几节需要重做，单独重跑那几节。

---

## 核心特性

| 特性 | 说明 |
|---|---|
| **只做章节测验** | 检测到考试（有倒计时）、作业，自动跳过 |
| **真实鼠标点击** | 用 Playwright 的真实点击选中选项，绕过 `aria-hidden` 封锁 |
| **题型全支持** | 单选（`li[role=radio]`）、多选（`li[role=checkbox]`）、填空、问答 |
| **交前自检** | 提交前检查每题是否真的被选中，一题没选就不提交 |
| **自动确认弹窗** | 点「提交」后自动点掉「确定」 |
| **状态验证** | 必须看到「已完成」或「已提交」才判成功，不看按钮就返回 |
| **未及格重做** | 出现「未达到及格线，请重做」自动再答一遍 |
| **登录态复用** | 直接读刷课项目的 `state.json`，不用重复扫码 |

---

## 快速开始

### 1. 环境准备

```bash
pip install playwright
playwright install chromium
```

### 2. 配置

```bash
cp config.example.json config.json
```

`course_url` 填**和刷课项目同一个课程地址**（必须带 `enc` 参数）。如果你已经跑过刷课项目，直接复制它的 `config.json` 里的 `course_url` 即可。

### 3. 登录

如果你已经跑过 `chaoxing-video`，把 `state.json` 复制过来：

```bash
cp ../chaoxing-video/state.json ./state.json
```

或者单独登录：

```bash
python quiz.py --login
```

### 4. 先试水一节

```bash
python quiz.py --limit 1
```

看日志：

```
===== [1/1] 1.3 国防的职能与使命【中】 (jobCount=1) =====
--- 章节 1.3 ---
  题目: 共3 单选2 多选1 其他0 已填3
    Q1 [single] picked=1
    Q2 [single] picked=1
    Q3 [multi] picked=2
  作答检查: 3/3 题已选
  已点提交 (Btn_blue_1 marleft10 workBtnIndex)
  已确认弹窗
  提交结果: ok
```

### 5. 全量跑

```bash
python quiz.py
```

---

## 命令参数

```bash
python quiz.py                # 跑全部待做章节测验
python quiz.py --limit 5      # 只跑前 5 节
python quiz.py --dry          # 只扫描不提交
python quiz.py --login        # 只做登录并保存 state.json
```

---

## 题型识别原理

超星的题目容器是 `.TiMu`，一道题一个。题型判定**不能靠 `input` 类型**，要看 `li` 的 `role` 属性：

### 单选题

```html
<div class="TiMu singleQuesId" data="405887897">
  <div class="Zy_TItle">【单选题】...</div>
  <ul class="Zy_ulTop">
    <li role="radio" class="before-after" aria-label="A 80；25选择">
      <label class="fl before">
        <input type="radio" name="answer405887897" value="A">&nbsp;&nbsp;A
      </label>
      <a class="fl after">80；25</a>
    </li>
    ...
  </ul>
</div>
```

判定：`li[role="radio"]`，或 `li` 的 class 含 `before-after` 但不含 `checkbox`。

### 多选题

```html
<div class="TiMu singleQuesId" data="405887733">
  <div class="Zy_TItle">【多选题】...</div>
  <ul class="Zy_ulTop">
    <li role="checkbox" class="before-after-checkbox" onclick="addcheck(405887733);"
        aria-label="A 以邻为善、以邻为伴选择">
      <label class="fl before">...</label>
    </li>
  </ul>
</div>
```

判定：`li[role="checkbox"]`，或 class 含 `before-after-checkbox`，或 `onclick` 含 `addcheck`。

> ⚠️ **关键坑**：多选题的 `li` 内部**没有** `input[type=checkbox]`，`input` 在别处由 JS 同步。所以不能用 `li.querySelector('input[type=checkbox]')` 来筛，那样会得到空数组，一题都选不上。

---

## 点击方式为什么这么讲究

这是整个项目最反直觉的地方。

### 失败的方式

```javascript
// ❌ 完全无效
document.querySelector('li').click();

// ❌ 完全无效
document.querySelector('label').click();

// ❌ 完全无效
document.querySelector('input[type=checkbox]').click();
```

**原因**：超星的选项 `ul` 带 `aria-hidden="true"`。浏览器在可访问性层面把它当成「对辅助技术不可见」，导致 **JS 触发的合成 click 事件被直接吞掉**。

实测对比：

```javascript
// JS el.click() 之后
{ cbs: [false, false, false, false], hidden: '' }   // 什么都没发生

// Playwright 真实点击之后
{ cbs: [true, false, false, false], hidden: 'A' }   // 成功选中
```

### 成功的方式

```python
# ✅ 方式 A：Playwright 真实鼠标事件
loc = frame.locator(".TiMu").nth(q - 1).locator("li").nth(li_idx)
loc.scroll_into_view_if_needed(timeout=3000)
loc.click(timeout=4000, force=True)
```

```javascript
// ✅ 方式 B：直接调页面业务函数
const m = li.getAttribute('onclick').match(/addcheck\((\d+)\)/i);
if (m && typeof addcheck === 'function') addcheck(m[1]);
```

项目里两种都用：优先 A，A 失败自动降级到 B。

---

## 作答策略

| 题型 | 策略 |
|---|---|
| 单选 | 从所有可选项中随机选 1 个 |
| 多选 | 随机选 `max(2, ceil(总数/2))` 个（至少 2 个） |
| 填空 | 填配置里的 `answer_fill_text`（默认「正确」） |
| 问答 | 填配置里的 `answer_essay_text`（默认「根据课程内容作答。」） |

**为什么不查答案**：查答案需要题库或 AI，成本高且不可靠。这个项目的目标是「提交完成」，不是「拿高分」。随机选对率约 25%（单选），够及格线就行。

---

## 提交链路

```
1. 点「提交」按钮（class 含 Btn_blue_1）
2. 等确认弹窗出现（#confirmSubWin）
3. 点「确定」
4. 等 30 秒，检测结果：
   ├─ 出现「未达到及格线，请重做」 → 自动重答一遍
   ├─ 出现「验证码」弹窗          → 判失败，需人工
   ├─ 出现「已完成/已提交/查看答案」→ 判成功
   └─ 仍是「待完成」              → 判失败
```

**注意**：提交前会先跑 `verify_answered()` 检查每题都有答案。如果一题都没选上，直接 `fail:no-answers` 不提交，避免提交空卷。

---

## 跳过什么

| 检测项 | 判定依据 |
|---|---|
| 考试 | 页面文本含「考试」或 URL 含 `exam`，或存在 `.examTime / .countdown / .timer` |
| 已完成 | 页面文本含「已完成 / 已提交 / 查看答案 / 重做」 |
| 无测验 | 点「章节测验」tab 后找不到 `doHomeWorkNew` frame |

---

## 踩过的坑

### 1. `input.click()` 无效 → 全部题目交白卷

见上文「点击方式」章节。`aria-hidden` 是罪魁祸首。

### 2. 多选题一题都选不上

第一版筛选条件写的是：

```javascript
const checkLis = [...t.querySelectorAll('li')].filter(li =>
    li.querySelector('input[type=checkbox]:not([disabled])'));
```

但纯多选题的 `li` 里**根本没有 input**，`input` 是 `addcheck()` 通过 JS 同步到别处的。所以 `checkLis` 永远是空数组，一题没选，然后 `fail:no-answers`。

修正：按 `li[role]`、class、`onclick` 属性来判断，**不要依赖 li 内部有 input**。

### 3. 用 `page.content()` 找题目 → 误判

超星页面里有隐藏的预制弹窗（`#confirmSubWin`、`#verifyCodeWin`、`#submitBack` 等），全文匹配会命中一堆隐藏元素。

修正：所有检测都基于**真实可见性**（`getBoundingClientRect` + `checkVisibility`）。

### 4. 只点「提交」不点「确定」→ 一直卡在确认弹窗

确认弹窗是异步出现的，点完提交要等 1-2 秒才弹。第一版点完提交直接查结果，永远查不到。

修正：加 15 秒轮询，找到含「确认提交」文本的**可见**弹窗，再点里面的「确定」。

### 5. 提交后只看有没有「已完成」二字 → 误判成功

有时提交后页面还是「待完成」，但因为它同时含有「已完成」字样（比如导航栏里的其他元素），会被误判。

修正：`submitted && !stillPending` 双条件判定。

---

## 实测数据

一门 133 节的课程，128 节有章节测验：

| 结果 | 数量 |
|---|---|
| 成功（jobCount 1 → 0） | **128** |
| 失败 | **0** |

首次全量跑：125 成功 / 3 失败。失败的 3 节（7.9、12.15、16.6）全部是**纯多选题**，就是上面第 2 条坑。修完后重跑这 3 节，全部通过。

---

## 目录结构

```
chaoxing-quiz/
├── quiz.py                 # 主程序
├── config.json             # 配置（.gitignore 已排除）
├── config.example.json     # 配置模板
├── state.json              # 登录态（.gitignore 已排除）
├── profile/                # 浏览器持久化数据（.gitignore 已排除）
└── logs/
    └── quiz_YYYYMMDD_HHMMSS.log
```

---

## 常见问题

**Q: 提交后 jobCount 还是 1？**
看日志里的「提交结果」。如果是 `fail:no-answers` 说明题目没选上；如果是 `fail:unconfirmed` 说明没等到结果确认。把日志里那节的详细记录发出来。

**Q: 能不能保证正确率？**
不能。随机作答，单选约 25% 正确率。目的是清任务点，不是拿高分。

**Q: 会不会做到作业/考试？**
不会。脚本会检测倒计时元素和「考试」文本，命中直接跳过。

**Q: 已经做过的章节会重复做吗？**
不会。`scan_chapters()` 只挑 `jobCount >= 1` 的，且进入后还会检测「已完成」标记。

**Q: 分数太低被要求重做怎么办？**
日志里会显示「未达到及格线，请重做」，脚本会自动再答一遍。如果还是不行，那节需要你手动做。

**Q: 视频还没刷完能直接做测验吗？**
技术上行，但超星一般要求先看完视频。建议先用 [chaoxing-video](../chaoxing-video) 把视频刷完。

---

## 配套项目

- **[chaoxing-video](../chaoxing-video)** — 视频任务点自动化（jobCount 2 → 1）

---

## 免责声明

本项目仅供学习 Playwright 自动化技术使用。使用者需自行承担使用后果，作者不对任何账号处罚、学业影响承担责任。请遵守所在学校的相关规定。

---

## License

MIT
