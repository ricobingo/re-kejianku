# RE 互动课件库

上课入口（只收藏这个）：<https://ricobingo.github.io/re-kejianku/>

账号已从 ricober33 改为 ricobingo。书签如果还指着 `https://ricober33.github.io/...` 会 404，请改成上面的地址。

## 稳定路径规则

- 每节课使用独立目录：`/re2/unit-6/6a1/`。
- 更新课件时只替换该目录内的文件并提升 `versions.json` 中的版本号；目录名和公开网址不变。
- 新课只新增目录，并在根目录 `index.html` 的 `CATALOG` 中增加一条记录。
- 旧入口 `https://ricobingo.github.io/re-agent-re2-u6/` 永久保留并跳转到本库。

## 当前内容

- RE2 Unit 6：6A1、6A2、6B1、6B2、Units 5–6 Quiz
- RE2 Unit 7：7A2 The Flower Trade

## GitHub Pages

部署工作流是 `.github/workflows/pages.yml`。权限只有 `contents: read`、`pages: write`、`id-token: write`。OIDC audience 显式申请 `https://github.com/ricobingo`。不要用内置的 pages-build-deployment：改名后它仍会拿出 audience `https://github.com/ricober33`。

仓库里的 `*.mp4` 是源视频，不打进 Pages artifact。工作流会排除源视频、`.git`、`.github`、构建脚本、缓存和依赖目录，再把每支课的视频压到原来的 `video/*.mp4` 路径后上传，上课页面不用改。打包结果必须小于 1000 MiB，低于 Pages 的 1 GiB 上限。本地实测整包约 590 MiB。

### 怎样确认 Pages 是绿的

1. 合并到 `main` 后，打开仓库 Settings → Pages → Build and deployment，Source 选 **GitHub Actions**（做一次即可，用来停掉仍带着 ricober33 audience 的内置部署）。
2. 打开 Actions 里的 **Deploy GitHub Pages**。推到 `main` 的那次运行应为绿色。`build` 日志里的 `Pages payload` 应小于 `1048576000`，`deploy` 日志里的 OIDC `aud` 应为 `https://github.com/ricobingo`。
3. 浏览器打开 <https://ricobingo.github.io/re-kejianku/> ，应看到课件目录，而不是 404。再打开一节带视频的课，确认视频能播放。
