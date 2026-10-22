# Handoff — 2026-10-22（第 15 卷，承接 handoff-2026-10-22.md §131）

> 上一卷：`docs/maintainer/handoff-2026-10-22.md`（§125–§131）　·　下一卷：（本文件为最新）
> 本卷记三件事：**本地全套件跑错了 worktree**（所以那句"4584 passed"根本不是关于分支的证据）、
> **issue 的验收标准与既有门禁互相打架**（以及怎么收场）、以及**像素门禁的同树噪声实测值**
> （它比容差高，门禁因此会报一个谁也没改过的改动）。

---

## 132.1 我把全套件跑在了错误的 worktree 里

**发生了什么**。#3641 的分支是 `feat/register-page`，worktree 在 `/tmp/wt-register`。本地全套件
跑出 6 failed / 4584 passed / 2 errors，看着像"基线已知红 + 几个环境噪音"，我据此判定"不是我
造成的"。

**它跑错了地方**。那次跑的 checkout 里 `docs/maintainer/handoff-2026-10-22.md` 有 **807 行**
（超过 800 行上限，所以 `test_handoff_size_limit` 红了）——而 §131 的稿子当时正好写到 807 行，
说明那次的 checkout 是**写 handoff 的那一棵树**，不是分支树。第二个旁证：那次跑的失败名单里
**没有** `test_quickstart_claims`，而分支上这条**已经**被 #3641 改红了（CI 上 `1 failed,
4522 passed`）。

**为什么会跑错**：主 checkout 停在 `docs/handoff-2026-10-22-round3` 分支上，我在那棵树里跑门禁
脚本（`sync_site_facts.py` 等），顺手把"全套件"也开了。

**代价**：本地那条"全绿"结论整条作废——它测的是另一棵树。**CI 抓到了真问题，本地没抓到。**

**那两条 teardown error 的归属也要重判**。它们报的是 `docs/index.html` 被改写。取证：
`/tmp/wt-register/docs/index.html` 的 mtime 是 **16:00:52**，而那次套件的运行窗口是
**15:54–16:02**——写入就发生在窗口里。写它的是**门禁脚本**（`sync_site_facts.py` 写模式会
无条件重写镜像，`docs/index.html` 在它的镜像清单里）。`tests/conftest.py` 的夹具
"checkout 不是 fixture"比的是 **(size, mtime)**，所以一次**内容相同**的覆写也会被判成"改写"。
隔离后重跑那两条 + handoff 尺寸那一条：**7 passed**。

> **教训**：本地全套件必须在**被测分支自己的 worktree** 里跑，而且日志里要留下"在哪棵树跑"的
> 证据（路径 + `git log -1`）。没有路径的"全套件跑过了"**不算证据**——它可能量的是另一棵树。
> 同理，**跑门禁脚本和跑套件不要在同一棵树上并行**：门禁脚本会写文件，而套件里有夹具在盯着
> 文件被写。

## 132.2 issue 的验收标准与既有门禁互相打架

CI 在 #3694 上抓到**唯一**一条失败：

```
FAILED tests/test_quickstart_claims.py::test_the_quickstart_says_pairing_is_not_needed_for_search
AssertionError: the Track A card must say search needs no pairing code — otherwise the
Start-page link reads as another step the reader owes the installer
```

**为什么会打架**。#3641 的验收标准写着「首页快速开始区无"注册/配对"字样」，而
`tests/test_quickstart_claims.py`（#3500 留下的）把**一句英文措辞**钉在**同一个区块**里：

```
assert "Search needs no pairing code" in track_a_card()
```

两个要求**不可能同时成立**。这不是我抄错，是 issue 的验收标准比那条门禁**更早**写下、而且没有
人发现它俩冲突——直到有人同时按两条做。

**我按哪个收场**：按 **issue**（首页那一块不再有 配对/register 字样），并把门禁从"钉一句措辞"
改成"钉住那个**主张**"。措辞确实被换掉了，但**主张没丢**：

| | 改前 | 改后 |
|---|---|---|
| 英文 | Search needs **no pairing code**: the installer already **registered your node**. | Search needs **nothing else**: the installer already **set you up**. |
| 中文 | 检索**不需要配对码**：安装器已经替你**注册了节点**。 | 检索**不需要额外步骤**：安装器已经替你**接好了**。 |

改写后主张反而更完整：旧句里有两个注册字样（"pairing code" + "registered your node"），
新句把它们都去掉了，而"检索不需要额外步骤"这个意思**一字不少**。

**门禁改成什么样**（`tests/test_quickstart_claims.py`）：

- 详情面 `docs/quickstart.md` 仍必须写着 `no pairing code`——**配对码是什么，在那儿解释**；
- 首页 Track A 卡片必须仍然说"检索不需要额外的东西"（正则 `search needs (no\b|nothing\b)`）；
- 卡片上的 Start 页链接必须仍然标着**可选**（`optional` / `可选`）；
- 上面三条**两份拷贝都要查**：页面的 fallback 文本，和 JavaScript 真正渲染的**词典**——
  词典是另一份会自己漂移的拷贝，而且**开着 JS 的读者看到的是它**；
- 中英文各查一遍。

**这不是"改测试让代码通过"**：门禁的**判据从措辞变成了主张**，而且**变严了**——多查一份拷贝
（词典）、多查一种语言（中文）、多查一个词（optional）。**7/7 变异全红**（断言级 rc=1）：

1. 主张改成要求（英文 fallback）→ 红
2. 主张被删（英文 fallback）→ 红
3. `optional` 被删（英文 fallback）→ 红
4. 英文词典丢主张 → 红
5. 英文词典丢 `optional` → 红
6. 中文词典把主张改成要求 → 红
7. 中文词典丢 `可选` → 红

反向对照（只加注释）**绿**。

> **教训**：issue 的验收标准与既有门禁冲突时，**两边都要显式收场并说清楚**——不能悄悄改门禁
> （那是"让 CI 绿"），也不能把 CI 放着红。收场的正确形状是：**保住门禁要保的东西**（主张），
> 放弃它没在保的东西（措辞），并且**让门禁比原来更严**（多查一份拷贝、一种语言）。

## 132.3 像素门禁的同树噪声：实测比容差高

`tests/test_pixel_capture_determinism.py`（本地全套件里红过一次）报：

```
AssertionError: 1280x800: 5 pixel(s) differ by more than the tolerance 3 between two trees that
differ by one character (max step 11)
```

**它不是 #3641 造成的**。取证方式：写了个探针，**同一棵树、同一页、连续拍 8 张**，看哪两张
不一样。**origin/main 上同样拍出来**，而且坐标一模一样：

| 视口 | 同树噪声 | 坐标 | max step |
|---|---|---|---|
| 390×844 | 4 px | x74..315, y717..719 | 5 |
| 1280×800 | 5 px | x471..472, y1357..1359 | 11 |

（390 宽那 4 个像素是**一条横线的两个端点**——搜索框圆角的两侧；基线在 390 宽上甚至更吵：
第 0–3 张在两个状态之间来回跳。）

**所以**：门禁的"停稳"判据（连着 3 张相同）通常把这个吸收掉，但**跨树**时两棵树可能各自停在
**不同的噪声状态**上——于是"差一个字符的两棵树"报出 5 个像素的差，而页面一个字都没变。
**容差 3 比同树噪声低**（实测 4~6 px / max step 11），也就是说**门禁自己的噪声地板比它的容差高**。

**留给 owner 的一个决定**（不是我能单方面定的）：

- 门禁比的是"两棵树"，而噪声是"同树"——**要么**门禁在比之前把"同树噪声"吸掉（每棵树多拍几张
  取多数状态），**要么**承认噪声地板并把它算进判据（"≤ N 个像素的差算噪声"）。现在这条测试
  要求**严格 0**，所以它**会偶发假红**。
- 这条测试在 CI 上是跳过的（CI 不装 Playwright），所以这个假红只咬本地和收尾的人。

**#3641 自己的像素判定不受影响**：scoped 口径跑的是**框外 0 像素**（§131.6），新页按 §131.5 声明。

## 132.4 现在的状态

- **PR #3694**（#3641 功能）：分支 `feat/register-page`，4 个提交（含门禁修正 `2688dfdd`），
  CI 重跑中。
- **PR #3695**（handoff §131 + 本卷 §132）：分支 `docs/handoff-2026-10-22-round6`。
- **本地全套件**（在被测分支自己的 worktree `/tmp/wt-register` 里重跑，`git log -1` = 分支头）：
  **4 failed / 4586 passed / 29 skipped / 0 errors**，523.91s。那 4 条**就是基线的已知红**
  （`test_http_utils` ×3 + `test_agent_autostart_parity` ×1）——**多出来的一条失败都没有，
  error 也归零**（上一次那 2 条 error 的成因见 §132.1）。
- **本地证据清单**：#3641 变异 **15/15**、`--allow-new` 变异 **10/10**、门禁改写变异 **7/7**，
  全部断言级变红；scoped 像素 **框外 0 像素**；站点测试 388 passed；门禁脚本全绿。
