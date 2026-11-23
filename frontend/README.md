# 前端工作台

第一部分提供任务列表、报告详情、历史入口和仓库/PR 提交表单，沿用现有后端接口。页面仅展示接口返回的数据；默认后端返回 Mock，合法提交目前返回 501，页面明确提示未创建任务。

## 启动与检查

使用项目要求的 Node 22.12+，在 frontend 目录执行：

```sh
npm ci
npm run dev
npm test
npm run build
```

本地后端默认位于 `http://127.0.0.1:8000`，Vite 代理 `/api` 与 `/health`。后端启动方法见根目录 README。可通过 `CODERISK_API_TARGET` 环境变量调整代理目标。

## 浏览器验收

```sh
# Windows 使用本机 Microsoft Edge；Linux 先安装测试浏览器
npx playwright install --with-deps chromium
npm run test:browser
```

Windows 已安装 Edge 时可直接运行 `npm run test:browser`。测试自动启动 4173 端口的 Vite，分别检查桌面和手机尺寸，使用团队共享 fixture 和拦截后的 HTTP 响应，无需密钥。截图保存在 test-results；失败时保留截图和 trace。

`npm test` 覆盖加载/空/错误、列表到详情与历史、任务失败、报告未就绪、分析限制、证据转义、表单校验、提交防重和 501/fake 202。Fake 202 只出现在测试里，浏览器 UI 没有成功假开关。

这些属于第一部分的页面和接口消费验收，不代表真实业务端到端审查已完成。真实轮询/分页/重试、完整跨模块链路和容器验证按后续阶段接入。
