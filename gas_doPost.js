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
// 売上集計表の自動更新
// 「集計用」シート（承認済みデータ）を担当者名ごとに集計し、
// 「集計表」シートに手作業で用意された担当者一覧表（A列:担当者名, B列:拠点,
// C列:エリア, D:セリング件数, E:セリング金額, F:増加件数, G:増加金額,
// H:今スト件数, I:今スト金額, J:売上実績件数(CSV), K:売上実績金額(CSV)）の
// D〜K列を埋める。
// A列（担当者名）・B列（拠点）・C列（エリア）・見出し行には一切触れない。
// このスクリプトを売上集計用スプレッドシートに開いてから拡張機能＞Apps Scriptで
// 開いた場合は、スプレッドシートのメニューバーに「📊 売上集計」→「集計を更新」が
// 追加される（onOpen）。メニューが出ない場合は、Apps Scriptエディタの関数選択で
// updateSalesSummary を選んで手動実行するか、createSalesSummaryTrigger を一度だけ
// 実行して自動更新（1時間毎）を設定する。
// ==========================================

var SALES_RAW_SHEET_NAME = "集計用";
var SALES_IMPORT_SHEET_NAME = "売上データ";
var SALES_SUMMARY_SHEET_NAME = "集計表";

// 集計表シート側（担当者一覧表）のレイアウト
var PERSON_TABLE_FIRST_ROW = 3;    // 1〜2行目が見出し、3行目から担当者データ
var PERSON_TABLE_COL_NAME = 1;     // A: 担当者名
var PERSON_TABLE_COL_SUMMARY_START = 4; // D: セリング件数（D〜Kの8列分を書き込む）

// 集計用シートの列（0始まり、views/sales_view.py の full_row と対応させること）
var COL_BRANCH = 2;
var COL_AREA = 3;
var COL_APPLICANT = 1;
var COL_CATEGORY = 8;
var COL_STATUS = 61;
var SELLING_START = 9;    // 5件 x (記号,販売数,単価)
var INCREASE_START = 24;  // 5件 x (記号,サイクル,単価,数量)
var STOP_START = 44;      // 5件 x (記号,数量,単価)

// CSV取り込み後の売上データシート側の列（0始まり）
var IMPORT_COL_QTY_ACTUAL = 9;   // 納品実績数量
var IMPORT_COL_PRICE_ACTUAL = 10; // 売上実績税抜単価

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

function updateSalesSummary() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var rawSheet = ss.getSheetByName(SALES_RAW_SHEET_NAME);
  var summarySheet = ss.getSheetByName(SALES_SUMMARY_SHEET_NAME);
  var ui;
  try { ui = SpreadsheetApp.getUi(); } catch (e) { ui = null; }

  if (!rawSheet || !summarySheet) {
    var msg = "シートが見つかりません（" + SALES_RAW_SHEET_NAME + " / " + SALES_SUMMARY_SHEET_NAME + "）。";
    if (ui) ui.alert(msg);
    return;
  }

  var data = rawSheet.getDataRange().getValues();
  var rows = data.slice(1); // 見出し行を除く

  // 担当者名（申請者）ごとに承認済みデータだけを集計する
  // （「申請中」「破棄」はここで除外されるので、集計表には含まれない）。
  var byPerson = {}; // name -> {branch, sellingCount, sellingAmount, increaseCount, increaseAmount, stopCount, stopAmount}

  rows.forEach(function (row) {
    if (String(row[COL_STATUS]).trim() !== "承認済み") return;

    var name = String(row[COL_APPLICANT]).trim();
    if (!name) return;
    var branch = String(row[COL_BRANCH]).trim();
    var category = String(row[COL_CATEGORY]).trim();
    var totals = computeSalesRowTotals(row);

    if (!byPerson[name]) {
      byPerson[name] = {
        branch: "", sellingCount: 0, sellingAmount: 0,
        increaseCount: 0, increaseAmount: 0, stopCount: 0, stopAmount: 0,
      };
    }
    if (branch) byPerson[name].branch = branch;

    if (category === "セリング") {
      byPerson[name].sellingCount += totals.count;
      byPerson[name].sellingAmount += totals.amount;
    } else if (category === "増加") {
      byPerson[name].increaseCount += totals.count;
      byPerson[name].increaseAmount += totals.amount;
    } else if (category === "理由あり今スト") {
      byPerson[name].stopCount += totals.count;
      byPerson[name].stopAmount += totals.amount;
    }
  });

  // 売上データシート（CSV取り込み）の実績も担当者ごとに集計する（承認フロー無し＝全件対象）。
  var importSheet = ss.getSheetByName(SALES_IMPORT_SHEET_NAME);
  var bySalesPerson = {}; // name -> {count, amount}
  if (importSheet) {
    var importData = importSheet.getDataRange().getValues();
    var importRows = importData.slice(1);
    importRows.forEach(function (row) {
      var name = String(row[COL_APPLICANT]).trim(); // 担当者名列はCOL_APPLICANTと同じ列位置(1)
      if (!name) return;
      var qty = Number(row[IMPORT_COL_QTY_ACTUAL]) || 0;
      var price = Number(row[IMPORT_COL_PRICE_ACTUAL]) || 0;
      if (!bySalesPerson[name]) bySalesPerson[name] = { count: 0, amount: 0 };
      bySalesPerson[name].count += qty;
      bySalesPerson[name].amount += qty * price;
    });
  }

  writeSalesPersonTable_(summarySheet, byPerson, bySalesPerson);

  if (ui) ui.alert("集計表を更新しました（" + new Date().toLocaleString() + "）");
}

// 集計表シートに手作業で用意された担当者一覧（A列:担当者名, B列:拠点）に沿って、
// D列（セリング件数）〜K列（売上実績金額(CSV)）を担当者名で突き合わせて書き込む。
// A列・B列・C列（エリア。あとで手入力するため触らない）・見出し行には一切触れない。
// 該当データが無い担当者は各項目とも空欄にする（0円は表示しない、入力画面の金額表示と同じ扱い）。
function writeSalesPersonTable_(sheet, byPerson, bySalesPerson) {
  var lastRow = sheet.getLastRow();
  if (lastRow < PERSON_TABLE_FIRST_ROW) return;

  var numRows = lastRow - PERSON_TABLE_FIRST_ROW + 1;
  var names = sheet.getRange(PERSON_TABLE_FIRST_ROW, PERSON_TABLE_COL_NAME, numRows, 1).getValues();

  var output = names.map(function (r) {
    var name = String(r[0]).trim();
    if (!name) return ["", "", "", "", "", "", "", ""];
    var p = byPerson[name];
    var s = bySalesPerson[name];
    return [
      p ? (p.sellingCount || "") : "",
      p ? (p.sellingAmount || "") : "",
      p ? (p.increaseCount || "") : "",
      p ? (p.increaseAmount || "") : "",
      p ? (p.stopCount || "") : "",
      p ? (p.stopAmount || "") : "",
      s ? (s.count || "") : "",
      s ? (s.amount || "") : "",
    ];
  });

  sheet.getRange(PERSON_TABLE_FIRST_ROW, PERSON_TABLE_COL_SUMMARY_START, output.length, 8).setValues(output);
}

// メニューが表示されない（スクリプトがスプレッドシートに紐付いていない）場合はこれを
// Apps Scriptエディタから一度だけ手動実行すると、1時間ごとに自動更新されるようになる。
function createSalesSummaryTrigger() {
  ScriptApp.newTrigger("updateSalesSummary")
    .timeBased()
    .everyHours(1)
    .create();
}
