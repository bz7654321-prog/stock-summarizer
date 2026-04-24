function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("종목 분석")
    .addItem("종목코드표 업데이트", "updateStockCodes")
    .addItem("종목코드만 채우기", "fillStockCodes")
    .addItem("선택 행 재무지표 채우기", "fillSelectedFinancialMetrics")
    .addItem("전체 행 재무지표 채우기", "fillFinancialMetrics")
    .addSeparator()
    .addItem("선택 행 분석하기", "analyzeSelectedStock")
    .addItem("전체 행 분석하기", "analyzeAllStocks")
    .addToUi();
}

function analyzeSelectedStock() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const row = sheet.getActiveRange().getRow();

  if (row === 1) {
    SpreadsheetApp.getUi().alert("제목 행이 아니라 종목명이 있는 행을 선택해주세요.");
    return;
  }

  analyzeRow(sheet, row);
}

function analyzeAllStocks() {
  const sheet = SpreadsheetApp.getActiveSheet();

  if (!isAnalysisSheet(sheet)) {
    SpreadsheetApp.getUi().alert("원래 종목 분석 시트에서 실행해주세요. '종목코드' 시트에서는 실행하지 마세요.");
    return;
  }

  const lastRow = sheet.getLastRow();

  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("분석할 종목이 없습니다.");
    return;
  }

  for (let row = 2; row <= lastRow; row++) {
    const stockName = sheet.getRange(row, 1).getValue();

    if (stockName) {
      analyzeRow(sheet, row);
      Utilities.sleep(1500);
    }
  }

  SpreadsheetApp.getUi().alert("전체 종목 분석이 완료되었습니다.");
}

function analyzeRow(sheet, row) {
  if (!isAnalysisSheet(sheet)) {
    SpreadsheetApp.getUi().alert("원래 종목 분석 시트에서 실행해주세요. '종목코드' 시트에서는 실행하지 마세요.");
    return;
  }

  const stockName = sheet.getRange(row, 1).getValue();
  let stockCode = sheet.getRange(row, 2).getValue();

  if (!stockName) {
    SpreadsheetApp.getUi().alert("A열에 종목명을 입력해주세요.");
    return;
  }

  if (!stockCode || stockCode === "코드 확인 필요") {
    stockCode = getStockCodeFromCodeSheet(stockName);

    if (stockCode) {
      sheet.getRange(row, 2).setNumberFormat("@");
      sheet.getRange(row, 2).setValue(stockCode);
    } else {
      sheet.getRange(row, 2).setValue("코드 확인 필요");
      stockCode = "미확인";
    }
  }

  const per = sheet.getRange(row, 3).getValue();
  const pbr = sheet.getRange(row, 4).getValue();
  const roe = sheet.getRange(row, 5).getValue();
  const opMargin = sheet.getRange(row, 6).getValue();
  const recentIssue = sheet.getRange(row, 7).getValue();

  sheet.getRange(row, 8).setValue("분석 중...");

  const prompt = makeStockPrompt(stockName, stockCode, per, pbr, roe, opMargin, recentIssue);
  const result = callGemini(prompt);

  sheet.getRange(row, 8).setValue(result);
  sheet.getRange(row, 8).setWrap(true);
}

function fillStockCodes() {
  const sheet = SpreadsheetApp.getActiveSheet();

  if (!isAnalysisSheet(sheet)) {
    SpreadsheetApp.getUi().alert("원래 종목 분석 시트에서 실행해주세요. '종목코드' 시트에서는 실행하지 마세요.");
    return;
  }

  const lastRow = sheet.getLastRow();

  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("종목명이 없습니다.");
    return;
  }

  for (let row = 2; row <= lastRow; row++) {
    const stockName = sheet.getRange(row, 1).getValue();
    const existingCode = sheet.getRange(row, 2).getValue();

    if (stockName && (!existingCode || existingCode === "코드 확인 필요")) {
      const code = getStockCodeFromCodeSheet(stockName);

      if (code) {
        sheet.getRange(row, 2).setNumberFormat("@");
        sheet.getRange(row, 2).setValue(code);
      } else {
        sheet.getRange(row, 2).setValue("코드 확인 필요");
      }
    }
  }

  SpreadsheetApp.getUi().alert("종목코드 입력이 완료되었습니다.");
}

function fillSelectedFinancialMetrics() {
  const sheet = SpreadsheetApp.getActiveSheet();

  if (!isAnalysisSheet(sheet)) {
    SpreadsheetApp.getUi().alert("원래 종목 분석 시트에서 실행해주세요. '종목코드' 시트에서는 실행하지 마세요.");
    return;
  }

  const row = sheet.getActiveRange().getRow();

  if (row === 1) {
    SpreadsheetApp.getUi().alert("제목 행이 아니라 종목명이 있는 행을 선택해주세요.");
    return;
  }

  fillFinancialMetricsForRow(sheet, row);
  SpreadsheetApp.getUi().alert("선택 행 재무지표 입력이 완료되었습니다.");
}

function fillFinancialMetrics() {
  const sheet = SpreadsheetApp.getActiveSheet();

  if (!isAnalysisSheet(sheet)) {
    SpreadsheetApp.getUi().alert("원래 종목 분석 시트에서 실행해주세요. '종목코드' 시트에서는 실행하지 마세요.");
    return;
  }

  const lastRow = sheet.getLastRow();

  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("분석할 종목이 없습니다.");
    return;
  }

  for (let row = 2; row <= lastRow; row++) {
    const stockName = sheet.getRange(row, 1).getValue();

    if (stockName) {
      fillFinancialMetricsForRow(sheet, row);
      Utilities.sleep(700);
    }
  }

  SpreadsheetApp.getUi().alert("전체 행 재무지표 입력이 완료되었습니다.");
}

function fillFinancialMetricsForRow(sheet, row) {
  const stockName = sheet.getRange(row, 1).getValue();
  let stockCode = sheet.getRange(row, 2).getValue();

  if (!stockName) {
    return;
  }

  if (!stockCode || stockCode === "코드 확인 필요") {
    stockCode = getStockCodeFromCodeSheet(stockName);

    if (stockCode) {
      sheet.getRange(row, 2).setNumberFormat("@");
      sheet.getRange(row, 2).setValue(stockCode);
    } else {
      sheet.getRange(row, 2).setValue("코드 확인 필요");
      return;
    }
  }

  sheet.getRange(row, 3, 1, 4).setValue("조회 중...");

  const metrics = getFinancialMetrics(stockCode);

  sheet.getRange(row, 3).setValue(metrics.per || "확인 필요");
  sheet.getRange(row, 4).setValue(metrics.pbr || "확인 필요");
  sheet.getRange(row, 5).setValue(metrics.roe || "확인 필요");
  sheet.getRange(row, 6).setValue(metrics.opMargin || "확인 필요");
}

function getFinancialMetrics(stockCode) {
  const metrics = {
    per: "",
    pbr: "",
    roe: "",
    opMargin: ""
  };

  const code = String(stockCode).trim().padStart(6, "0");

  try {
    const summaryUrl = "https://api.finance.naver.com/service/itemSummary.nhn?itemcode=" + code;

    const summaryResponse = UrlFetchApp.fetch(summaryUrl, {
      method: "get",
      muteHttpExceptions: true,
      headers: {
        "User-Agent": "Mozilla/5.0"
      }
    });

    if (summaryResponse.getResponseCode() === 200) {
      const summaryText = summaryResponse.getContentText();
      const summary = JSON.parse(summaryText);

      if (summary.per !== undefined && summary.per !== null && summary.per !== 0) {
        metrics.per = summary.per;
      }

      if (summary.pbr !== undefined && summary.pbr !== null && summary.pbr !== 0) {
        metrics.pbr = summary.pbr;
      }

      if (summary.roe !== undefined && summary.roe !== null && summary.roe !== 0) {
        metrics.roe = summary.roe;
      }
    }
  } catch (error) {}

  try {
    const pageUrl = "https://finance.naver.com/item/main.naver?code=" + code;

    const pageResponse = UrlFetchApp.fetch(pageUrl, {
      method: "get",
      muteHttpExceptions: true,
      headers: {
        "User-Agent": "Mozilla/5.0"
      }
    });

    let html = pageResponse.getContentText("EUC-KR");

    if (!html || html.length < 1000) {
      html = pageResponse.getContentText("UTF-8");
    }

    if (!metrics.roe) {
      const roe = extractFromHtml(html, /ROE\(지배주주\)[\s\S]*?<em[^>]*>(-?\d+(?:\.\d+)?)<\/em>/i);
      if (roe) metrics.roe = roe;
    }

    const opMargin = extractFromHtml(html, /영업이익률[\s\S]*?<em[^>]*>(-?\d+(?:\.\d+)?)<\/em>/i);
    if (opMargin) metrics.opMargin = opMargin;

    if (!metrics.roe) {
      const text = cleanHtmlText(html);
      const roeFallback = extractMetricAfterLabel(text, "ROE");
      if (roeFallback) metrics.roe = roeFallback;
    }

    if (!metrics.opMargin) {
      const text = cleanHtmlText(html);
      const opFallback = extractMetricAfterLabel(text, "영업이익률");
      if (opFallback) metrics.opMargin = opFallback;
    }

  } catch (error) {}

  return metrics;
}

function extractFromHtml(html, regex) {
  try {
    const match = String(html).match(regex);
    if (match && match[1]) {
      return String(match[1]).trim();
    }
    return "";
  } catch (error) {
    return "";
  }
}

function extractMetricAfterLabel(text, label) {
  try {
    const idx = text.indexOf(label);

    if (idx === -1) {
      return "";
    }

    const slice = text.substring(idx, idx + 700);
    const matches = slice.match(/-?\d{1,3}(?:,\d{3})*(?:\.\d+)?/g);

    if (!matches || matches.length === 0) {
      return "";
    }

    for (let i = 0; i < matches.length; i++) {
      const value = matches[i].replace(/,/g, "");

      if (isFinite(Number(value))) {
        return value;
      }
    }

    return "";
  } catch (error) {
    return "";
  }
}

function updateStockCodes() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let codeSheet = ss.getSheetByName("종목코드");

  if (!codeSheet) {
    codeSheet = ss.insertSheet("종목코드");
  }

  codeSheet.clear();

  const url = "https://github.com/FinanceData/stock_master/raw/master/stock_master.csv.gz";

  try {
    const response = UrlFetchApp.fetch(url, {
      method: "get",
      muteHttpExceptions: true,
      headers: {
        "User-Agent": "Mozilla/5.0"
      }
    });

    const responseCode = response.getResponseCode();

    if (responseCode !== 200) {
      SpreadsheetApp.getUi().alert("종목코드표 다운로드 실패. 응답 코드: " + responseCode);
      return;
    }

    const blob = response.getBlob();
    const unzipped = Utilities.ungzip(blob);
    const csvText = unzipped.getDataAsString("UTF-8");

    const data = Utilities.parseCsv(csvText);

    if (!data || data.length < 2) {
      SpreadsheetApp.getUi().alert("종목코드 CSV를 읽지 못했습니다.");
      return;
    }

    const header = data[0];

    const symbolIndex = header.indexOf("Symbol");
    const nameIndex = header.indexOf("Name");
    const listingIndex = header.indexOf("Listing");
    const marketIndex = header.indexOf("Market");

    if (symbolIndex === -1 || nameIndex === -1) {
      SpreadsheetApp.getUi().alert("CSV에서 Symbol 또는 Name 컬럼을 찾지 못했습니다.");
      return;
    }

    const values = [["종목명", "종목코드", "시장"]];

    for (let i = 1; i < data.length; i++) {
      const row = data[i];

      const code = String(row[symbolIndex] || "").trim().padStart(6, "0");
      const name = String(row[nameIndex] || "").trim();
      const listing = listingIndex !== -1 ? String(row[listingIndex] || "").trim() : "True";
      const market = marketIndex !== -1 ? String(row[marketIndex] || "").trim() : "";

      if (name && /^\d{6}$/.test(code) && listing === "True") {
        values.push([name, code, market]);
      }
    }

    codeSheet.getRange(1, 1, values.length, 3).setNumberFormat("@");
    codeSheet.getRange(1, 1, values.length, 3).setValues(values);
    codeSheet.autoResizeColumns(1, 3);

    SpreadsheetApp.getUi().alert("종목코드표 업데이트 완료: " + (values.length - 1) + "개 종목");

  } catch (error) {
    SpreadsheetApp.getUi().alert("종목코드표 업데이트 실패: " + error.message);
  }
}

function getStockCodeFromCodeSheet(stockName) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const codeSheet = ss.getSheetByName("종목코드");

    if (!codeSheet) {
      return null;
    }

    const lastRow = codeSheet.getLastRow();

    if (lastRow < 2) {
      return null;
    }

    const values = codeSheet.getRange(2, 1, lastRow - 1, 2).getValues();
    const targetName = normalizeStockName(stockName);

    for (let i = 0; i < values.length; i++) {
      const name = normalizeStockName(values[i][0]);
      const code = String(values[i][1]).trim();

      if (name === targetName && /^\d{6}$/.test(code)) {
        return code;
      }
    }

    for (let i = 0; i < values.length; i++) {
      const name = normalizeStockName(values[i][0]);
      const code = String(values[i][1]).trim();

      if ((name.includes(targetName) || targetName.includes(name)) && /^\d{6}$/.test(code)) {
        return code;
      }
    }

    return null;

  } catch (error) {
    return null;
  }
}

function isAnalysisSheet(sheet) {
  return sheet.getRange(1, 1).getValue() === "종목명";
}

function normalizeStockName(name) {
  return String(name)
    .replace(/\s/g, "")
    .replace(/\(주\)/g, "")
    .replace(/㈜/g, "")
    .replace(/보통주/g, "")
    .replace(/우선주/g, "")
    .replace(/ /g, "")
    .trim();
}

function cleanHtmlText(text) {
  return String(text)
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}

function makeStockPrompt(stockName, stockCode, per, pbr, roe, opMargin, recentIssue) {
  return `
너는 한국 주식 초보 투자자를 위한 종목 해설가다.

아래 종목에 대해 초보 투자자도 이해할 수 있게 설명해라.

종목명: ${stockName}
종목코드: ${stockCode || "미입력"}
PER: ${per || "미입력"}
PBR: ${pbr || "미입력"}
ROE: ${roe || "미입력"}
영업이익률: ${opMargin || "미입력"}
사용자가 적은 최근이슈: ${recentIssue || "미입력"}

다음 형식으로 작성해라.

[${stockName} 종목 이해 리포트]

1. 한 줄 요약
- 이 회사가 어떤 회사인지 한 문장으로 설명

2. 이 회사는 뭘로 돈을 버는가?
- 주요 사업과 매출 구조를 쉽게 설명

3. 시장은 이 회사를 어떤 종목으로 보는가?
- 단순 업종이 아니라 현재 시장에서 붙은 테마나 기대감을 설명
- 예: 증권주, 우주항공 관련주, 유리기판 관련주, AI 반도체 관련주 등

4. 최근 주목받는 이유
- 최근 주가나 관심도가 올라간 이유를 설명
- 사용자가 적은 최근이슈가 있으면 우선 반영
- 확실하지 않은 내용은 "확인 필요"라고 표시

5. 관련 테마
- 관련될 수 있는 테마를 3~5개 정리

6. 재무적으로 봐야 할 포인트
- 입력된 PER, PBR, ROE, 영업이익률을 해석
- 숫자가 "확인 필요" 또는 "미입력"이면 수치를 지어내지 말고 확인 필요라고 표시

7. 긍정 포인트
- 이 종목의 강점이나 기대 요인

8. 리스크 포인트
- 이 종목을 볼 때 조심해야 할 점

9. 초보 투자자용 해석
- 이 종목을 처음 보는 사람이 이해할 수 있게 쉽게 설명

10. 한 문장 결론
- 이 종목을 이해하는 핵심 문장

주의사항:
- 매수/매도 추천을 하지 마라.
- 확실하지 않은 사실은 단정하지 마라.
- 모르는 정보는 지어내지 말고 "확인 필요"라고 적어라.
- 최신 뉴스가 필요한 부분은 "최신 뉴스 확인 필요"라고 적어라.
- 단순 회사소개가 아니라, 왜 시장이 이 종목을 주목하는지 설명해라.
`;
}

function callGemini(prompt) {
  const apiKey = PropertiesService.getScriptProperties().getProperty("GEMINI_API_KEY");

  if (!apiKey) {
    return "오류: GEMINI_API_KEY가 설정되지 않았습니다. Apps Script의 프로젝트 설정 > 스크립트 속성에 API 키를 저장해주세요.";
  }

  const url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key=" + apiKey;

  const payload = {
    contents: [
      {
        parts: [
          {
            text: prompt
          }
        ]
      }
    ],
    generationConfig: {
      temperature: 0.3,
      maxOutputTokens: 2048
    }
  };

  const options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    const response = UrlFetchApp.fetch(url, options);
    const responseText = response.getContentText();
    const data = JSON.parse(responseText);

    if (data.error) {
      return "Gemini API 오류: " + data.error.message;
    }

    if (
      data.candidates &&
      data.candidates[0] &&
      data.candidates[0].content &&
      data.candidates[0].content.parts &&
      data.candidates[0].content.parts[0]
    ) {
      return data.candidates[0].content.parts[0].text;
    }

    return "분석 실패: 응답 형식이 예상과 다릅니다.\n" + responseText;

  } catch (error) {
    return "분석 중 오류 발생: " + error.message;
  }
}
