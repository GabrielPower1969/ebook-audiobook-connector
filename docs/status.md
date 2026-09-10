# 进度存档 · 2026-09-10

一句话：**听读同步这件事已经做完并在跑；下一步是把书库和它合并成一个网站。**
新会话从这里开始读，再按需翻 `merge-plan.md` / `figma-brief.md` / `naming.md`。

---

## 一、已经做完并验证过的

### 书房（现在就能用）

局域网服务在跑：`http://Gabriels-Mabook-Pro.local:8765`（13 本书）。
`scripts/start.command` 起、`scripts/stop.command` 彻底停。

| | 数字 |
|---|---|
| 已建成的书 | 13 本 |
| 段落 / 句子 | 44735 段 · 93438 句 |
| 词覆盖对齐率 | 哈利波特 **100%**，其余 93–96% |
| 正文↔音频独立核对 | 36209 段，93.9% ≥ 0.90，低于 0.55 仅 46 段（0.13%） |
| 词典 | ECDICT 裁剪到本库 29123 词，按首字母分 26 片 |
| 转写成本 | 124 小时音频 → 9 小时（mlx，约 14× 实时） |

阅读器：句级点播与高亮、练习模式（复读 / 跟读停顿 / 0.5×）、双击查词（音标 + 中英释义 +
考纲标签 + 全书例句）、生词本导出 TSV、四主题（含墨水屏）、离线包可双击 `index.html` 打开。

### 已确认的关键事实（别再重新验证）

- **launchd 读不了 `~/Documents`**（TCC）。开机自启必须把项目放在 `~/audiobook-connector`
  这类非保护目录。实验证据：LaunchAgent 里 `ls` 项目目录返回 `Operation not permitted`。
- **`fetch()` 在 `file://` 下被拦，但 `<script src>` 不拦**。离线包靠这个跑。
- **R2 出站流量免费**、10 GB 存储免费；本库 9.1 GB（620 个 mp3 占 5.04 GB）。
- **对齐率要按词数报**，不能按段落数——书末索引是几百个两词段落，没人朗读。
- 现有设备身份是 cookie，不是 IP/MAC（IP 会变，手机 MAC 每网络随机，容器里都看不见）。

---

## 二、已定的决策

| | 决定 | 定于 |
|---|---|---|
| 仓库 | **合并成一个，名为 `flowgt-ebook`**，且必须 **private** | 2026-09-10 |
| 架构 | **R2 存文件 + Worker 放送**，Mac Mini 退为「工厂」只管加新书 | 2026-09-10 |
| 前端 | **重新设计**，委托书见 `figma-brief.md` | 2026-09-10 |
| 登录 | Google OAuth；匿名也能读，登录后进度并入账号 | 2026-09-10 |
| 非暴力沟通两版 | 建议留 A 版（36 章、129 kbps），弃 B 版（4 段、64 kbps） | 待你确认 |

---

## 三、还等你拍板的

1. **网站名与域名** —— 建议 `thespokenpage.com`（未注册，已双查），见 `naming.md`
2. **三本书归哪个分类**：谈判力（04/06）、非暴力沟通（07/02）、用户访谈（06/09）
3. **哈利波特拆不拆**成七个文件夹（建议拆）
4. **非暴力沟通 B 版**确认弃用
5. Wright 那本 *Lean Analytics Complete Guide* 要不要

---

## 四、下一步的执行顺序

```
0. 定名 + 注册域名（Cloudflare Registrar）        ← 卡住 OAuth 回调和 Access 域名
1. 清 ebook 根目录 8 项 → build_index.py         ← 见 merge-plan §1
2. 建 flowgt-ebook 私有仓库，把两边合进去          ← 见 merge-plan §0
3. SQLite schema + 导入器（170 本书入库、抽封面）   ← 见 merge-plan §3.3 ERD
4. Google OAuth + 会话（吸收现有设备 cookie）
5. R2 + Worker：文件放送、Range、进度 API          ← 见 merge-plan §5
6. 前端按 Figma 稿实现（零构建静态页）
7. 安全 / 防污染 / SEO（见下）
8. 剩余有声书入库对齐 + TTS 试点
```

### 第 7 步的清单（新增，之前没写过）

**安全**
- Cloudflare Access 在最前面；Worker 侧再验一次 JWT（不信任「前面挡过了」）
- 所有写接口校验 `Origin` / `Sec-Fetch-Site`，防 CSRF
- 上传/导入只走 Mac 侧，Worker 不接受任何用户上传
- 速率限制：Cloudflare Rules，按 IP + 账号
- 密钥只进 Worker Secrets，永不进仓库；仓库私有也一样

**防污染**（内容与索引不被外部搞脏）
- 数据库唯一真相是 `library.json` + 内容哈希，导入时校验 sha256，对不上就拒绝
- 任何用户输入（笔记、书单名）落库前转义，渲染时不用 `innerHTML` 拼接
- 生词本、进度这类用户数据按账号隔离，Worker 侧强制 `WHERE user_id = ?`
- 派生物（封面、转写、对齐）全部可从源文件重建，被污染就整目录删掉重跑

**SEO**（注意：书的内容是私有的，能公开的只有「壳」）
- 公开页只有：首页、关于、方法论/博客。**书目和正文一律 `noindex` 且在 Access 后面**
- 首页做 server-rendered HTML（Worker 直出），不靠 JS 才能看到内容
- `sitemap.xml`、`robots.txt`、canonical、hreflang（zh-CN / en）
- Open Graph + Twitter Card，配一张品牌图
- JSON-LD：`WebSite` + `SoftwareApplication`；**不要**给私有藏书打 `Book` 结构化数据
- 十句名言可以做十个内容页（`/quotes/portable-magic` 之类），这是唯一天然适合 SEO 的素材

---

## 五、当前磁盘与仓库

- 代码仓库：`~/Documents/workspace/audiobook-connector`，已推 GitHub（`ebook-audiobook-connector`，**public**）
- 书库：`~/Documents/ebook`，9.1 GB，private git，只跟踪结构
- 备份：`backups/audiobook-connector-20260910-0935.tgz`（26 MB，含转写缓存与阅读进度）
- 打包产物已全部删除；磁盘剩约 22 GB
