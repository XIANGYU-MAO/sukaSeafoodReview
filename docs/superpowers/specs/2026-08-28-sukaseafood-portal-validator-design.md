# SukaSeafood 项目入口与 CSV Validator 设计

日期：2026-08-28

## 目标

在不改变现有图片审核和 API 行为的前提下，把 SukaSeafood 根路径改为公开的双语项目入口，并把桌面版 CSV Validator 发布为浏览器本地工具。

## 公共路由

- `https://findai.top/sukaseafood/`：项目介绍与工具入口。
- `https://findai.top/sukaseafood/validator/`：CSV Validator。
- `https://findai.top/sukaseafood/review/`：现有 Image Review，保持登录与审核逻辑不变。
- `https://findai.top/sukaseafood/api/*`：现有 API，保持不变。
- 无尾斜杠的项目、Validator 和 Review 路径永久重定向到对应尾斜杠路径。
- `www.findai.top/sukaseafood*` 继续永久重定向到 `findai.top` 的相同路径。

## 页面设计

项目页使用海洋蓝绿色视觉，包含：项目名称、双语项目定位、数据工作流概览、CSV Validator 与 Image Review 两张主要入口卡片，以及说明这两个工具分别用于结构校验和训练图片审核。

页面右上角提供中文 / English 切换，语言选择保存在浏览器 `localStorage`。Validator 保留原有双语切换、模板下载、示例数据、拖放上传、校验、预览和规范化 CSV 下载功能。

## 架构

继续使用现有 `review-web` Nginx 容器，不新增数据库、API 或公开端口。Vite 构建前生成两组静态资源：项目页位于容器 `/portal/`，Validator 位于容器 `/validator/`；现有 Review SPA 仍位于容器根路径。

独立 `findai-infra` Caddy 网关按公共前缀路由并重写到对应容器内部路径。发布顺序固定为先发布包含静态资源的 Review Web，再发布网关配置，避免网关先指向尚不存在的资源。

## 安全与隐私

- CSV 解析、校验和规范化只在浏览器内完成，文件不上传服务器。
- Validator 原有内联 CSS 和 JavaScript 在构建时拆分为同源静态资源，以继续满足现有严格 CSP，不为页面加入 `unsafe-inline`。
- 项目页和 Validator 无需登录；Review 与 API 的认证、Cookie 和 CSRF 行为不变。
- 不引入第三方 CDN、分析脚本或外部字体。

## 验收标准

1. 根路径显示双语项目介绍，不再跳转到 Review。
2. 两个入口分别打开 Validator 与 Image Review。
3. 项目页中英文切换即时生效并在刷新后保留。
4. Validator 包含 WWF、Recipe、Cooking suitability、CV model、Landing 和全部 PriceCatcher CSV 类型，上传与下载均在本地完成。
5. Review 和 API 现有测试及线上健康检查继续通过。
6. Nginx、Compose、Caddy 和公网预检覆盖三个页面路径与 API。

## 非目标

- 不修改数据库 Schema、后端字段、审核业务逻辑或账号权限。
- 不向 Validator 增加服务端上传、文件存储或协作功能。
