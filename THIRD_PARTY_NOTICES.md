# Third-party notices / 第三方来源与许可

根目录 MIT LICENSE 适用于本项目原创代码和文档，不覆盖下列第三方内容。保留各来源的署名、链接与许可条件。以下列出的是素材来源和分发范围，不是对实时旅游信息准确性的保证。

## ChinaTravel Sandbox

- 项目：[LAMDA-NeSy/ChinaTravel](https://github.com/LAMDA-NeSy/ChinaTravel)
- 数据：[LAMDA-NeSy/ChinaTravel-Sandbox](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox)
- 上游将沙盒数据集声明为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。代码许可证与数据许可证不同。
- `backend/scripts/fetch_data.py` 下载中文表并筛选杭州，记录来源和抓取日期。原始 Parquet 文件不在本仓库中。
- 地点 ID、名称和部分派生元数据用于对齐基础库；停留估计及人工修订是本项目处理结果，不是来源机构的实时验证或背书。使用派生数据时仍应保留 ChinaTravel 来源署名。

## Wikivoyage 攻略改编

`backend/app/assets/guides/pilot-v1.json` 的公开版包含 HZ-G01 至 HZ-G11，共 11 条结构化改编样本。

- 来源：[Wikivoyage — Hangzhou](https://en.wikivoyage.org/wiki/Hangzhou)，作者为 Wikivoyage contributors；作者记录见[页面历史](https://en.wikivoyage.org/w/index.php?title=Hangzhou&action=history)。
- 许可：[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。这部分结构化改编继续以 CC BY-SA 4.0 分发。
- 改动：中文概括、拆分为同游关系/人群/停留/季节等字段，并对齐项目地点 ID。来源元数据及采集日期保留在 JSON 中。
- 不包含官方旅游门户的本地研究样本、小红书内容或外部模型响应缓存。`training_allowed` 为本项目语料管理标志；本次发布不声称已进行模型训练。

## 城市照片

`frontend/public/questionnaire/` 的照片来自 Wikimedia Commons。完整文件名、作者、原始页与加工记录见该目录 [README](frontend/public/questionnaire/README.md)。

| 文件 | 作者 | 许可 |
| --- | --- | --- |
| `west-lake.jpg` | Peter Dowley | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) |
| `longjing.jpg` | Wishva de Silva | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |
| `tangqi.jpg` | Huandy618 | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |
| `lake-evening.jpg` | Morio | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |

这些图片适用其各自许可，包含尺寸、压缩和展示裁切等处理，不适用 MIT。地点详情图库注册表为空，后续添加的图片需要独立记录版权、来源与授权范围。

## 外部服务与软件依赖

高德地图、地点评分、路线与照片查询，以及模型和 Jev 服务，遵循其服务条款；本项目没有重新授权这些服务数据。需要自行取得相应 key 和使用权限。地图上的版权标识应保留。

Python/npm 依赖及字体、图标保留各自许可证，具体版本见 `backend/uv.lock` 与 `frontend/package-lock.json`，许可证见安装包。Fontsource 字体包的字体许可与应用代码许可独立，构建和重新分发时应保留相应声明。
