# SukaSeafood 候选图片采集器

**中文** | [English](README.md)

这个本地工具根据当前鱼种目录，从 Fish-Vista、iNaturalist、GBIF、Wikimedia
Commons、Atlas of Living Australia（ALA）、OBIS、NOAA Photo Library 和
Smithsonian Open Access 收集带许可证的候选图片 metadata，并写入稳定的
`output/candidates.csv` manifest。候选记录不会自动成为训练数据。

## 配置当前鱼种目录

采集器只支持 schema version `2`。管理后台下载的最新配置会同时写入每个鱼种当前的
`candidate_count`。先复制受跟踪的示例配置，再把虚构条目替换为
当前目录中的鱼种：

```powershell
Copy-Item .\species_config.example.json .\species_config.json
python .\collect_fish_images.py --config .\species_config.json --source inat --source gbif --source ala --source obis --max-per-species 100 --minimum-total-per-species 300 --maximum-total-per-species 500
python .\collect_fish_images.py --config .\species_config.json --source commons --species FISH_A --resume
```

每个 active 条目必须有唯一的 `seafood_code`、中英文名和精确的
`scientific_name`。`inat_taxon_id` 与 `gbif_taxon_key` 可以是 `null`；此时采集器
会用 scientific name 自动解析精确 taxon。自动解析需要明确指定时，使用正整数
override。`commons_category` 默认是 `Category:<scientific_name>`，
`fish_vista_filter` 默认是 scientific name。

未知字段、空文本、重复 code、负数 `candidate_count`、非正数 override 和非 version 2 的 schema 都会在
采集开始前被拒绝。

## 安装和采集

在本目录的 Windows 环境中执行：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

`--source` 可选 `all`、`fish-vista`、`inat`、`gbif`、`commons`、`ala`、
`obis`、`noaa` 或 `smithsonian`，需要多个来源时可重复填写。没有 `--species`
时，会采集所有已配置鱼种。单次生成时会先在本次 CSV 内按“鱼种 + 图片地址”去重。
不加 `--resume` 会重写本地 `output/candidates.csv`；加上后会保留旧行，再把新行合并
去重。管理后台导入时还会按来源身份和同鱼种图片地址再次去重。

Smithsonian 需要免费 API Key：勾选该来源后使用管理后台生成的命令，或手动添加
`--smithsonian-api-key YOUR_KEY`。也可以设置本地环境变量
`SMITHSONIAN_API_KEY`。密钥不需要上传到审核系统。默认只收 metadata；
`--download-images` 为可选项。

`--minimum-total-per-species 300` 表示当前候选数低于 300 时才启动该鱼种的采集；
`--maximum-total-per-species 500` 表示启动后最多补到服务器总数 500。最大值不能小于
最小值。每个来源仍受 `--max-per-species` 限制，所以来源图片不足或导入判重时，一次
采集可能仍未达标；先导入 CSV，再从管理后台重新下载最新配置继续补采即可。

## 归档服务器已有候选

如需把服务器中已经存在的全部候选图片归档到本机，请先在管理后台“采集与导入”中
下载“导出全部候选 CSV”，然后运行：

```powershell
python .\collect_fish_images.py --download-manifest .\sukaseafood-all-candidates.csv --output-dir "G:\sukaseafood-candidate-archive"
```

`--download-manifest` 只读取指定 CSV，不会重新采集 metadata，也不会向审核服务器上传
回执或改变任何审核、下载状态。本地进度写入目标目录的 `candidates.csv`，每处理 10 行
保存一次。再次运行同一命令时会校验 `local_path` 与 SHA-256；校验成功的文件直接跳过，
仅重试缺失、损坏和上次失败的行。

归档模式遇到 Wikimedia Commons 原图限流或失效时，可以通过 Commons 官方 API 获取
1600 像素缩略图作为本地归档回退，但不会改写 CSV 中的原始图片地址。该回退只用于候选
归档；训练原图同步绝不会用缩略图代替原图。

## 来源与许可证规则

采集器只保留 `CC0`、Public Domain、`CC BY`、`CC BY-SA`、`CC BY-NC` 和
`CC BY-NC-SA` 媒体，并保留 source URL、attribution 和 source metadata 以便后续
核验。带 `ND`、没有许可证或不能识别的许可证会被排除。

iNaturalist 在精确 taxon 解析后只查询 Research Grade observation；GBIF 只保留
带许可证的 still-image media；Fish-Vista 使用配置中的精确 filter；Commons 使用
配置中的 category；ALA 和 OBIS 只保留精确学名且许可证可用的图片；Smithsonian
只保留精确学名的 CC0 图片；NOAA 只保留标题含精确学名并明确标注 NOAA 机构署名的
图片，第三方 Courtesy 图片会跳过。单个鱼种/来源失败时会输出错误，其他来源仍会继续采集。

## 审核与原图

审核在 online system 中进行，不通过本地采集器完成。训练原图由 `local_sync/`
处理；本目录不是本地审核流程。必须保留 provenance 和 license 字段，且只有 online
审核流程批准后，候选行才能用于训练。

## 测试

```powershell
python -m pytest tests -q
```
