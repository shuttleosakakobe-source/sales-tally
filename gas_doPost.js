/**
 * gidを正しく解決してシートを取得するヘルパー
 * ※ SpreadsheetApp.openByUrl() はURLの #gid=... / ?gid=... を無視するため、
 *    そのまま getSheets()[0] を使うと「一番左のシート」しか取れない。
 *    kensaku（メンテナンス依頼アプリ）・campaign-tally（キャンペーン集計アプリ）の
 *    gas_doPost.jsと同じ実装。
 */
function getSheetFromUrl(url) {
  var ss = SpreadsheetApp.openByUrl(url);
  var gidMatch = url.match(/[?&#]gid=(\d+)/);
  if (gidMatch) {
    var gid = Number(gidMatch[1]);
    var sheets = ss.getSheets();
    for (var i = 0; i < sheets.length; i++) {
      if (sheets[i].getSheetId() === gid) {
        return sheets[i];
      }
    }
  }
  return ss.getSheets()[0];
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var action = data.action;

    // ==========================================
    // 上乗せ活動入力（セリング／増加／理由あり今スト、1行追加のみ）
    // 対象: views/sales_view.py の SALES_SHEET_URL
    // ==========================================
    if (action === "SUBMIT_SALES_ENTRY") {
      var targetUrl = data.target_sheet_url;
      var sheet = getSheetFromUrl(targetUrl);
      sheet.appendRow(data.full_row);
      return ContentService.createTextOutput(JSON.stringify({"status": "success"}))
        .setMimeType(ContentService.MimeType.JSON);

    // ==========================================
    // 管理職チェック（承認／破棄）：該当行を丸ごと updated_row で上書きする
    // ==========================================
    } else if (action === "UPDATE_SALES_STATUS") {
      var targetUrl = data.target_sheet_url;
      var sheet = getSheetFromUrl(targetUrl);
      var rowIndex = data.row_index;
      var updatedRow = data.updated_row;
      var range = sheet.getRange(rowIndex, 1, 1, updatedRow.length);
      range.setValues([updatedRow]);
      return ContentService.createTextOutput(JSON.stringify({"status": "success"}))
        .setMimeType(ContentService.MimeType.JSON);

    // ==========================================
    // 日報CSV一括インポート（権限0専用・承認不要）：複数行をまとめて追加する
    // 対象: views/sales_view.py の SALES_IMPORT_SHEET_URL（売上データシート）
    // ==========================================
    } else if (action === "IMPORT_SALES_CSV") {
      var targetUrl = data.target_sheet_url;
      var sheet = getSheetFromUrl(targetUrl);
      var rows = data.rows;
      if (rows && rows.length > 0) {
        var startRow = sheet.getLastRow() + 1;
        var numCols = rows[0].length;
        sheet.getRange(startRow, 1, rows.length, numCols).setValues(rows);
      }
      return ContentService.createTextOutput(JSON.stringify({"status": "success", "imported": rows ? rows.length : 0}))
        .setMimeType(ContentService.MimeType.JSON);
    }

    return ContentService.createTextOutput(JSON.stringify({"status": "error", "message": "未定義のアクション: " + action}))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({"status": "error", "message": err.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ==========================================
// 月次集計（担当者ごと）・日次集計（拠点ごと）の自動更新
//
// 集計の考え方：
// - 月次集計＝担当者ごと、日次集計＝拠点ごと（拠点は「担当者マスタ」シートの
//   A列:担当者名/B列:拠点から引く。集計用シート側は申請者本人が入力した拠点列を優先）。
// - 予定売上・実績売上・今スト金額（CSVベース）は「売上データ」シート（CSV取込）から、
//   セリング・上乗せ（増加）・理由あり今スト金額は「集計用」シート（承認済みのみ）から
//   それぞれ集計する。
// - 今スト金額（CSVベース）＝伝票納品結果種別が「2」（持ち越し中・未確定）の行を除いた、
//   (予定数量×予定単価 − 実績数量×実績単価) の合計。
// - 理由あり今スト（スタッフが手入力・承認済み）の金額は、予定売上・今スト金額の
//   両方からマイナスする（予定売上＝より実態に近い予定に調整／今スト金額＝
//   まだ理由が説明されていない未解明の差分だけが残るようにする）。
//
// このスクリプトを売上集計用スプレッドシートに開いてから拡張機能＞Apps Scriptで
// 開いた場合は、スプレッドシートのメニューバーに「📊 売上集計」→「集計を更新」が
// 追加される（onOpen）。メニューが出ない場合は、Apps Scriptエディタの関数選択で
// updateSalesSummary を選んで手動実行するか、createSalesSummaryTrigger を一度だけ
// 実行して自動更新（1時間毎）を設定する。
// ==========================================

var SALES_RAW_SHEET_NAME = "集計用";
var SALES_IMPORT_SHEET_NAME = "売上データ";
var STAFF_MASTER_SHEET_NAME = "担当者マスタ";
var MONTHLY_SHEET_NAME = "月次集計";
var DAILY_SHEET_NAME = "日次集計";

// 担当者マスタシート側（担当者一覧表）のレイアウト
var STAFF_MASTER_FIRST_ROW = 3;   // 1〜2行目が見出し、3行目から担当者データ
var STAFF_MASTER_COL_NAME = 1;    // A: 担当者名
var STAFF_MASTER_COL_BRANCH = 2;  // B: 拠点

// 集計用シートの列（0始まり、views/sales_view.py の full_row と対応させること）
var COL_TIMESTAMP = 0;
var COL_APPLICANT = 1;
var COL_BRANCH = 2;
var COL_CATEGORY = 8;
var COL_STATUS = 61;
var SELLING_START = 9;    // 5件 x (記号,販売数,単価)
var INCREASE_START = 24;  // 5件 x (記号,サイクル,単価,数量)
var STOP_START = 44;      // 5件 x (記号,数量,単価)

// 売上データシート（CSV取込）側の列（0始まり、CSVの列順と対応させること）
var IMPORT_COL_REPORT_DATE = 0;    // 日報入力確定年月日（例: "20261014"）
var IMPORT_COL_SCHEDULED_DATE = 1; // 本来納品予定年月日（空白の場合あり）
var IMPORT_COL_STAFF_NAME = 2;     // 担当者名
var IMPORT_COL_RESULT_CODE = 6;    // 伝票納品結果種別
var IMPORT_COL_QTY_PLANNED = 7;    // 納品予定数量
var IMPORT_COL_PRICE_PLANNED = 8;  // 売上予定税抜単価
var IMPORT_COL_QTY_ACTUAL = 9;     // 納品実績数量
var IMPORT_COL_PRICE_ACTUAL = 10;  // 売上実績税抜単価

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("📊 売上集計")
    .addItem("集計を更新", "updateSalesSummary")
    .addToUi();
}

// この申請1件分の「個数」「金額」を計算する（アプリ入力画面の計算式と同じ）
function computeSalesRowTotals(row) {
  var category = row[COL_CATEGORY];
  var count = 0, amount = 0;

  if (category === "セリング") {
    for (var i = 0; i < 5; i++) {
      var base = SELLING_START + i * 3;
      var s = Number(row[base + 1]) || 0;
      var p = Number(row[base + 2]) || 0;
      count += s;
      amount += s * p;
    }

  } else if (category === "増加") {
    for (var j = 0; j < 5; j++) {
      var b = INCREASE_START + j * 4;
      var cycle = Number(row[b + 1]) || 0;
      var pr = Number(row[b + 2]) || 0;
      var qty = Number(row[b + 3]) || 0;
      count += qty;
      if (cycle) {
        amount += pr * (4 / cycle) * qty;
      }
    }

  } else if (category === "理由あり今スト") {
    for (var k = 0; k < 5; k++) {
      var sb = STOP_START + k * 3;
      var sq = Number(row[sb + 1]) || 0;
      var sp = Number(row[sb + 2]) || 0;
      count += sq;
      amount += sq * sp;
    }
  }

  return { count: count, amount: amount };
}

// "20261014" -> {month: "2026/10", day: "2026/10/14"}（CSVの日報確定年月日用）
function parseReportDate_(dateStr) {
  var s = String(dateStr).trim();
  if (s.length !== 8) return null;
  var y = s.substring(0, 4), m = s.substring(4, 6), d = s.substring(6, 8);
  return { month: y + "/" + m, day: y + "/" + m + "/" + d };
}

// "2026/10/14 09:00:00" -> {month: "2026/10", day: "2026/10/14"}（申請のタイムスタンプ用）
function parseTimestamp_(ts) {
  var s = String(ts).trim();
  if (s.length < 10) return null;
  return { month: s.substring(0, 7), day: s.substring(0, 10) };
}

function loadStaffBranchMap_(ss) {
  var sheet = ss.getSheetByName(STAFF_MASTER_SHEET_NAME);
  var map = {};
  if (!sheet) return map;
  var lastRow = sheet.getLastRow();
  if (lastRow < STAFF_MASTER_FIRST_ROW) return map;
  var numRows = lastRow - STAFF_MASTER_FIRST_ROW + 1;
  var data = sheet.getRange(STAFF_MASTER_FIRST_ROW, STAFF_MASTER_COL_NAME, numRows, 2).getValues();
  data.forEach(function (r) {
    var name = String(r[0]).trim();
    var branch = String(r[1]).trim();
    if (name) map[name] = branch;
  });
  return map;
}

function emptyBucket_() {
  return {
    plannedAmount: 0,    // CSVの予定売上（数量×単価の合計）
    actualAmount: 0,     // CSVの実績売上（数量×単価の合計）
    stopAmountCsv: 0,    // CSVベースの今スト金額（コード2を除く、予定-実績の合計）
    sellingAmount: 0,    // 承認済みセリング金額
    increaseAmount: 0,   // 承認済み上乗せ（増加）金額
    stopManualAmount: 0, // 承認済み理由あり今スト金額（手入力分）
    workingDaySet: {},   // 稼働日の集計用（日報入力確定年月日の重複排除。月次集計のみで使う）
  };
}

function updateSalesSummary() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var rawSheet = ss.getSheetByName(SALES_RAW_SHEET_NAME);
  var importSheet = ss.getSheetByName(SALES_IMPORT_SHEET_NAME);
  var monthlySheet = ss.getSheetByName(MONTHLY_SHEET_NAME);
  var dailySheet = ss.getSheetByName(DAILY_SHEET_NAME);
  var ui;
  try { ui = SpreadsheetApp.getUi(); } catch (e) { ui = null; }

  if (!rawSheet || !importSheet || !monthlySheet || !dailySheet) {
    var msg = "シートが見つかりません（" + SALES_RAW_SHEET_NAME + " / " + SALES_IMPORT_SHEET_NAME +
      " / " + MONTHLY_SHEET_NAME + " / " + DAILY_SHEET_NAME + "）。";
    if (ui) ui.alert(msg);
    return;
  }

  var staffBranch = loadStaffBranchMap_(ss);

  var byStaffMonth = {}; // "担当者|年月" -> バケット
  var byBranchDay = {};  // "拠点|日付" -> バケット

  function getBucket_(map, key) {
    if (!map[key]) map[key] = emptyBucket_();
    return map[key];
  }

  // --- 売上データ（CSV取込）を集計：予定売上・実績売上・今スト金額（CSVベース）の元になる ---
  var importRows = importSheet.getDataRange().getValues().slice(1);
  importRows.forEach(function (row) {
    var staffName = String(row[IMPORT_COL_STAFF_NAME]).trim();
    if (!staffName) return;
    var parsed = parseReportDate_(row[IMPORT_COL_REPORT_DATE]);
    if (!parsed) return;
    var branch = staffBranch[staffName] || "";

    var plannedAmount = (Number(row[IMPORT_COL_QTY_PLANNED]) || 0) * (Number(row[IMPORT_COL_PRICE_PLANNED]) || 0);
    var actualAmount = (Number(row[IMPORT_COL_QTY_ACTUAL]) || 0) * (Number(row[IMPORT_COL_PRICE_ACTUAL]) || 0);
    var resultCode = String(row[IMPORT_COL_RESULT_CODE]).trim();

    // 💡 予定売上の対象は「本来納品予定年月日」が空白でなく、かつ日報入力確定年月日以降
    //    （同日含む）の行だけに限定する。本来納品予定年月日が日報確定日より前＝
    //    過去から持ち越された予定（今回の集計期間の予定ではない）、空白＝そもそも
    //    事前の予定が無かった取引のため、どちらも「予定売上」には含めない。
    //    どちらも日付は "YYYYMMDD" の8桁文字列なので、文字列比較でそのまま前後判定できる。
    var scheduledDateStr = String(row[IMPORT_COL_SCHEDULED_DATE]).trim();
    var reportDateStr = String(row[IMPORT_COL_REPORT_DATE]).trim();
    var includeInPlanned = !!scheduledDateStr && scheduledDateStr >= reportDateStr;

    var mb = getBucket_(byStaffMonth, staffName + "|" + parsed.month);
    if (includeInPlanned) mb.plannedAmount += plannedAmount;
    mb.actualAmount += actualAmount;
    if (resultCode !== "2") mb.stopAmountCsv += (plannedAmount - actualAmount);
    mb.workingDaySet[parsed.day] = true; // 稼働日＝日報入力確定年月日の重複排除カウント

    if (branch) {
      var db = getBucket_(byBranchDay, branch + "|" + parsed.day);
      if (includeInPlanned) db.plannedAmount += plannedAmount;
      db.actualAmount += actualAmount;
      if (resultCode !== "2") db.stopAmountCsv += (plannedAmount - actualAmount);
    }
  });

  // --- 集計用（セリング・上乗せ・理由あり今スト、承認済みのみ）を集計 ---
  var rawRows = rawSheet.getDataRange().getValues().slice(1);
  rawRows.forEach(function (row) {
    if (String(row[COL_STATUS]).trim() !== "承認済み") return;
    var staffName = String(row[COL_APPLICANT]).trim();
    if (!staffName) return;
    var branch = String(row[COL_BRANCH]).trim() || staffBranch[staffName] || "";
    var category = String(row[COL_CATEGORY]).trim();
    var parsed = parseTimestamp_(row[COL_TIMESTAMP]);
    if (!parsed) return;
    var totals = computeSalesRowTotals(row);

    var mb = getBucket_(byStaffMonth, staffName + "|" + parsed.month);
    var db = branch ? getBucket_(byBranchDay, branch + "|" + parsed.day) : null;

    if (category === "セリング") {
      mb.sellingAmount += totals.amount;
      if (db) db.sellingAmount += totals.amount;
    } else if (category === "増加") {
      mb.increaseAmount += totals.amount;
      if (db) db.increaseAmount += totals.amount;
    } else if (category === "理由あり今スト") {
      mb.stopManualAmount += totals.amount;
      if (db) db.stopManualAmount += totals.amount;
    }
  });

  writeMonthlySummary_(monthlySheet, byStaffMonth, staffBranch);
  writeDailySummary_(dailySheet, byBranchDay);

  if (ui) ui.alert("集計表を更新しました（" + new Date().toLocaleString() + "）");
}

// 月次集計シート（2行目以降）を、担当者×年月の組み合わせごとに1行で丸ごと書き直す。
// 列：年月, 担当者名, 拠点, 稼働日, 予定売上(調整後), セリング金額, 上乗せ(増加)金額,
//     今スト金額(調整後), 実績売上
function writeMonthlySummary_(sheet, byStaffMonth, staffBranch) {
  var rows = Object.keys(byStaffMonth).map(function (key) {
    var parts = key.split("|");
    var name = parts[0], month = parts[1];
    var b = byStaffMonth[key];
    var branch = staffBranch[name] || "";
    var workingDays = Object.keys(b.workingDaySet).length;
    var plannedAdjusted = b.plannedAmount - b.stopManualAmount;
    var stopAdjusted = Math.max(0, b.stopAmountCsv - b.stopManualAmount);
    return [month, name, branch, workingDays, plannedAdjusted, b.sellingAmount, b.increaseAmount, stopAdjusted, b.actualAmount];
  });
  rows.sort(function (a, b) {
    if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
    return a[1] < b[1] ? -1 : (a[1] > b[1] ? 1 : 0);
  });

  var lastRow = sheet.getLastRow();
  if (lastRow >= 2) sheet.getRange(2, 1, lastRow - 1, 9).clearContent();
  if (rows.length > 0) sheet.getRange(2, 1, rows.length, 9).setValues(rows);
}

// 日次集計シート（2行目以降）を、拠点×日付の組み合わせごとに1行で丸ごと書き直す。
// 列：日付, 拠点, 予定売上(調整後), セリング金額, 上乗せ(増加)金額,
//     今スト金額(調整後), 実績売上
function writeDailySummary_(sheet, byBranchDay) {
  var rows = Object.keys(byBranchDay).map(function (key) {
    var parts = key.split("|");
    var branch = parts[0], day = parts[1];
    var b = byBranchDay[key];
    var plannedAdjusted = b.plannedAmount - b.stopManualAmount;
    var stopAdjusted = Math.max(0, b.stopAmountCsv - b.stopManualAmount);
    return [day, branch, plannedAdjusted, b.sellingAmount, b.increaseAmount, stopAdjusted, b.actualAmount];
  });
  rows.sort(function (a, b) {
    if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
    return a[1] < b[1] ? -1 : (a[1] > b[1] ? 1 : 0);
  });

  var lastRow = sheet.getLastRow();
  if (lastRow >= 2) sheet.getRange(2, 1, lastRow - 1, 7).clearContent();
  if (rows.length > 0) sheet.getRange(2, 1, rows.length, 7).setValues(rows);
}

// メニューが表示されない（スクリプトがスプレッドシートに紐付いていない）場合はこれを
// Apps Scriptエディタから一度だけ手動実行すると、1時間ごとに自動更新されるようになる。
function createSalesSummaryTrigger() {
  ScriptApp.newTrigger("updateSalesSummary")
    .timeBased()
    .everyHours(1)
    .create();
}
