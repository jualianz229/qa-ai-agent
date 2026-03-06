/**
 * Shared Live Jobs / Execution queue UI – GitHub Actions-style step list.
 * Used by Dashboard, Case Generator, E2E Automation, Visual Regression.
 */
(function (global) {
  "use strict";

  function cleanLogLines(lines) {
    if (!Array.isArray(lines)) return [];
    const cleaned = [];
    let lastSpinnerBase = "";
    for (const line of lines) {
      const text = String(line || "").trim();
      const spinnerMatch = text.match(/^[\-\|\\\/]\s*(AI\s+.*?\.\.\.)/i);
      if (spinnerMatch) {
        const base = spinnerMatch[1].toLowerCase();
        const cleanText = text.replace(/^[\-\|\\\/]\s*/, "");
        if (base === lastSpinnerBase && cleaned.length > 0) {
          cleaned[cleaned.length - 1] = cleanText;
        } else {
          lastSpinnerBase = base;
          cleaned.push(cleanText);
        }
      } else {
        lastSpinnerBase = "";
        cleaned.push(line);
      }
    }
    return cleaned;
  }

  function parseJobSteps(logLines, jobStatus) {
    const stepMap = {};
    const order = [];
    for (const line of logLines || []) {
      const text = String(line || "").trim();
      const m = text.match(/^\[STEP\]\s*(\d+)\s*\|\s*(.+)$/);
      if (!m) continue;
      const stepId = parseInt(m[1], 10);
      const msg = m[2].trim();
      const msgLower = msg.toLowerCase();
      if (msgLower === "done" || msgLower === "fail") {
        if (stepMap[stepId]) stepMap[stepId].status = msgLower === "fail" ? "fail" : "done";
      } else {
        if (!stepMap[stepId]) {
          stepMap[stepId] = { id: stepId, name: msg, status: "running" };
          order.push(stepId);
        } else {
          stepMap[stepId].name = msg;
          stepMap[stepId].status = "running";
        }
      }
    }
    const jobDone = String(jobStatus || "").toLowerCase() === "completed";
    const jobFail = String(jobStatus || "").toLowerCase() === "failed";
    order.forEach(function (id) {
      const s = stepMap[id];
      if (s && s.status === "running" && (jobDone || jobFail)) s.status = jobFail ? "fail" : "done";
    });
    return { steps: order.map(function (id) { return stepMap[id]; }), fullLog: (logLines || []).join("\n") };
  }

  function buildStepsHtml(steps, isRunning) {
    if (!steps || !steps.length) return "";
    return steps.map(function (s) {
      const icon = s.status === "done" ? "✓" : s.status === "fail" ? "✗" : (isRunning ? "○" : "✓");
      const iconClass = s.status === "done" ? "step-done" : s.status === "fail" ? "step-fail" : "step-running";
      return '<div class="job-step ' + iconClass + '" data-step-id="' + s.id + '">' +
        '<span class="job-step-icon">' + icon + '</span>' +
        '<span class="job-step-name">' + (s.name || "").replace(/</g, "&lt;").replace(/>/g, "&gt;") + '</span></div>';
    }).join("");
  }

  /** Parse last "Token used: N" from log lines (agent prints this). */
  function getTokenUsed(logLines) {
    if (!Array.isArray(logLines)) return null;
    for (var i = logLines.length - 1; i >= 0; i--) {
      var m = String(logLines[i] || "").match(/Token\s+used:\s*(\d+)/i);
      if (m) return parseInt(m[1], 10);
    }
    return null;
  }

  /**
   * Build steps list for a job card (no log output, no terminal frame).
   * @param {Object} job - job object with log_lines, status
   * @param {boolean} isRunning
   * @param {boolean} useCleanLog - use cleanLogLines for token parsing
   * @returns {{ steps, stepsHtml, hasSteps, tokenUsed }}
   */
  function buildStepsAndLogSection(job, isRunning, useCleanLog) {
    const rawLines = Array.isArray(job.log_lines) ? job.log_lines : [];
    const parsed = parseJobSteps(rawLines, job.status);
    const hasSteps = parsed.steps.length > 0;
    const stepsHtml = buildStepsHtml(parsed.steps, isRunning);
    const tokenUsed = getTokenUsed(rawLines);
    return {
      steps: parsed.steps,
      stepsHtml: stepsHtml,
      hasSteps: hasSteps,
      tokenUsed: tokenUsed
    };
  }

  function formatDurationSeconds(createdAt, updatedAt) {
    const createdMs = Date.parse(createdAt || "");
    const updatedMs = Date.parse(updatedAt || "") || Date.now();
    if (!Number.isFinite(createdMs)) return "-";
    return Math.max(0, Math.round((updatedMs - createdMs) / 1000)) + "s";
  }

  function sourceLabelFromMode(mode) {
    const m = String(mode || "").toLowerCase();
    if (m === "vrt_scan") return "VRT Scan";
    if (m === "automation_test") return "Automation";
    if (m === "retry_failed") return "Retry Failed";
    if (m === "safe_rerun") return "Safe Rerun";
    return "Scenario";
  }

  global.JobQueueUI = {
    cleanLogLines: cleanLogLines,
    parseJobSteps: parseJobSteps,
    buildStepsHtml: buildStepsHtml,
    buildStepsAndLogSection: buildStepsAndLogSection,
    getTokenUsed: getTokenUsed,
    formatDurationSeconds: formatDurationSeconds,
    sourceLabelFromMode: sourceLabelFromMode
  };
})(typeof window !== "undefined" ? window : this);
