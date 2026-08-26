# SukaSeafood Windows 训练集同步工具

这个工具由 Mao 在自己的 Windows 电脑上运行。它从管理后台导出的增量 CSV 读取服务器指定的操作，只让本机直接向外部来源站点获取新审核通过的原图。图片不会经过中国服务器，也不会存放在中国服务器；服务器只提供小型 CSV 并接收小型回执。

## 图形界面用法

1. 打开 `https://findai.top/sukaseafood/review`，进入管理后台，导出待同步的增量 CSV。
2. 解压整个 `SukaSeafoodTrainingSync` 目录，不要只复制 exe。双击 `SukaSeafoodTrainingSync.exe`。
3. 选择刚导出的 CSV 和本地训练集目录。这个目录应放在有稳定空间、会备份的本地磁盘上。
4. 如需代理，填写 HTTP/HTTPS 代理；留空时使用 Windows 进程环境中的代理设置。`NO_PROXY` 可让指定域名或内网地址绕过代理，显式代理也不会覆盖匹配的 `NO_PROXY`。
5. 点击开始。界面会显示当前候选、进度和结果。需要停止时点击取消；取消是协作式的，已安全提交到本地的操作会保留，重新运行即可继续。

鱼种代码是动态鱼种，不限于 SF001–SF005；工具按管理后台 CSV 中经过校验的 Windows 安全代码创建目录。

## 增量、失败重试和审核变化

- 只有新审核通过的 ADD 原图由 Mao 的电脑从外部来源直接下载。
- 相同候选、审核版本和操作已经成功时，后续批次或同一批次再次运行会跳过，不重复请求原图。
- 下载、解码或网络失败的项目不会记为成功；失败重试时重新运行同一 CSV 即可。
- 审核变化由服务器精确给出 ADD、MOVE 或 REMOVE。MOVE 只移动本地已有图片；REMOVE 把图片移入服务器指定的 `_removed/{batch_id}/...`，不会永久删除。
- 带旧路径的组合 ADD 只有在新图片已经验证并写入目标后才清理旧路径。

如果回执无法完整上传，工具会在训练集目录保存 `download_receipt-{batch_id}.json` 离线回执。网络恢复后可在命令行结合原始 CSV 重传，或者在管理后台上传离线回执。不要把 CSV 或回执令牌粘贴到命令行参数、日志或聊天中。

## `_removed` 恢复与备份

`_removed` 是可恢复区，不是回收站。需要恢复时，先停止同步，根据 `logs/` 中相应操作记录确认旧路径和目标路径，再从 `_removed/{batch_id}/...` 复制回训练集；保留原文件，直到确认恢复无误。不要在同步运行时手工移动这些文件。

本地图片、`canonical_manifest.csv`、`logs/` 和索引都应纳入定期备份。生产 PostgreSQL 保存审核业务数据；它与本机的小型 SQLite 恢复索引完全不同，不能互相替代。服务器不备份本地训练图片。

训练集根目录下的 `.sukaseafood-sync.sqlite3` 只记录幂等恢复所需的操作键、相对路径、哈希和回执状态：不保存图片字节、不保存原图地址、不保存回执令牌。它不是生产 PostgreSQL，也不通过服务器代理图片。

## 命令行

同一个 exe 在没有参数时打开中文图形界面；有参数时使用命令行，不会启动 Tk 窗口：

```powershell
.\SukaSeafoodTrainingSync.exe --version
.\SukaSeafoodTrainingSync.exe inspect .\batch.csv
.\SukaSeafoodTrainingSync.exe sync .\batch.csv D:\SukaSeafoodTraining
.\SukaSeafoodTrainingSync.exe sync .\batch.csv D:\SukaSeafoodTraining --no-submit
.\SukaSeafoodTrainingSync.exe submit-receipt .\download_receipt-BATCH.json --batch-csv .\batch.csv --dataset-root D:\SukaSeafoodTraining
```

## 校验下载的构建

发布的构建目录旁有 `SHA256SUMS.txt`。在其父目录运行：

```powershell
$expected = (Get-Content .\SHA256SUMS.txt).Split()[0]
$actual = (Get-FileHash .\SukaSeafoodTrainingSync\SukaSeafoodTrainingSync.exe -Algorithm SHA256).Hash
if ($actual -ine $expected) { throw "SHA-256 校验失败" }
```

校验失败时不要运行 exe，应重新取得完整构建目录。任何同步前都应确认训练集已有可恢复备份。
