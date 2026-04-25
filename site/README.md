# AI Knowledge Base Viewer

輕量級前端介面，用於瀏覽 `knowledge/articles/` 下的結構化知識條目。

## 快速啟動

從**專案根目錄**啟動 HTTP 伺服器：

```bash
cd /path/to/ai-knowledge-base
python3 -m http.server 8080
```

開啟瀏覽器訪問 [http://localhost:8080/site/](http://localhost:8080/site/)

> 必須從專案根目錄啟動，因為頁面透過 `/knowledge/articles/` 路徑載入 JSON 資料。

## 功能

| 功能 | 說明 |
|------|------|
| 日期篩選 | 下拉選單選擇特定日期，僅顯示當日採集的條目 |
| 關鍵字搜尋 | 搜尋標題、標籤、摘要，匹配的標籤會高亮顯示 |
| 排序 | 支援按日期或相關度升降冪排列 |
| 統計面板 | 顯示文章總數、採集天數、標籤數、平均相關度 |
| 卡片檢視 | 每則條目顯示 repo 連結、相關度分數、摘要（點擊展開）、標籤 |

## 技術棧

純靜態頁面，零依賴：

- HTML / CSS / Vanilla JavaScript
- 資料來源：`knowledge/articles/index.json` + 各條目 JSON 檔案

## 目錄結構

```
ai-knowledge-base/
├── site/
│   ├── index.html       # 前端頁面
│   └── README.md        # 本文件
└── knowledge/
    └── articles/        # JSON 知識條目（資料來源）
        ├── index.json
        └── *.json
```
