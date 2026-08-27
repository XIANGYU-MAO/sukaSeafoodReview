# SukaSeafood 候选图片采集器

**中文** | [English](README.md)

这个本地工具根据当前鱼种目录，从 Fish-Vista、iNaturalist、GBIF 和 Wikimedia
Commons 收集带许可证的候选图片 metadata，并写入稳定的
`output/candidates.csv` manifest。候选记录不会自动成为训练数据。

## 配置当前鱼种目录

采集器只支持 schema version `2`。管理后台下载的最新配置会同时写入每个鱼种当前的
`candidate_count`。先复制受跟踪的示例配置，再把虚构条目替换为
当前目录中的鱼种：

```powershell
Copy-Item .\species_config.example.json .\species_config.json
python .\collect_fish_images.py --config .\species_config.json --source all --max-per-species 100 --minimum-total-per-species 300
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

`--source` 可选 `all`、`fish-vista`、`inat`、`gbif` 或 `commons`。没有
`--species` 时，会采集所有已配置鱼种。`--resume` 会把后续结果合并进已有 manifest，
不会覆盖现有候选行。默认只收 metadata；`--download-images` 为可选项。

`--minimum-total-per-species 300` 表示服务器中每个鱼种至少希望有 300 个候选。采集器
根据配置中的 `candidate_count` 跳过已达标鱼种，只采集未达标鱼种的缺口，并在缺口
填满时停止。来源图片不足或导入判重时，一次采集可能仍未达标；先导入 CSV，再从
管理后台重新下载最新配置继续补采即可。

## 来源与许可证规则

采集器只保留 `CC0`、Public Domain、`CC BY`、`CC BY-SA`、`CC BY-NC` 和
`CC BY-NC-SA` 媒体，并保留 source URL、attribution 和 source metadata 以便后续
核验。带 `ND`、没有许可证或不能识别的许可证会被排除。

iNaturalist 在精确 taxon 解析后只查询 Research Grade observation；GBIF 只保留
带许可证的 still-image media；Fish-Vista 使用配置中的精确 filter；Commons 使用
配置中的 category。单个鱼种/来源失败时会输出错误，其他来源仍会继续采集。

## 审核与原图

审核在 online system 中进行，不通过本地采集器完成。训练原图由 `local_sync/`
处理；本目录不是本地审核流程。必须保留 provenance 和 license 字段，且只有 online
审核流程批准后，候选行才能用于训练。

## 测试

```powershell
python -m pytest tests -q
```
