(function () {
  "use strict";

  var dropZone = document.getElementById("dropZone");
  var fileInput = document.getElementById("fileInput");
  var selectBtn = document.getElementById("selectBtn");
  var uploadStatus = document.getElementById("uploadStatus");
  var progressFill = document.getElementById("progressFill");
  var statusMsg = document.getElementById("statusMsg");
  var resultSection = document.getElementById("resultSection");

  selectBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    fileInput.click();
  });
  dropZone.addEventListener("click", function () {
    fileInput.click();
  });
  fileInput.addEventListener("change", function () {
    if (fileInput.files.length > 0) {
      uploadFile(fileInput.files[0]);
    }
  });

  dropZone.addEventListener("dragover", function (e) {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });
  dropZone.addEventListener("dragleave", function () {
    dropZone.classList.remove("dragover");
  });
  dropZone.addEventListener("drop", function (e) {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) {
      uploadFile(e.dataTransfer.files[0]);
    }
  });

  function uploadFile(file) {
    dropZone.style.display = "none";
    uploadStatus.style.display = "block";
    progressFill.style.width = "10%";
    statusMsg.textContent = "正在上传...";

    var formData = new FormData();
    formData.append("file", file);

    fetch("/api/upload", { method: "POST", body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.task_id) {
          statusMsg.textContent = "上传成功，正在解析...";
          pollTask(data.task_id);
        } else {
          showError("上传失败: " + JSON.stringify(data));
        }
      })
      .catch(function (err) {
        showError("上传出错: " + err.message);
      });
  }

  function pollTask(taskId) {
    var attempts = 0;
    var maxAttempts = 120;

    function check() {
      fetch("/api/task/" + taskId)
        .then(function (r) { return r.json(); })
        .then(function (task) {
          attempts++;

          var statusMap = {
            pending: "等待中...",
            extracting: "正在解压...",
            scanning: "正在扫描目录...",
            identifying: "正在识别板卡和日志...",
            done: "完成",
            error: "出错",
          };
          statusMsg.textContent = (statusMap[task.status] || task.status) + " " + (task.message || "");
          progressFill.style.width = Math.min(task.progress * 100, 90) + "%";

          if (task.status === "done" || task.status === "error") {
            progressFill.style.width = "100%";
            uploadStatus.style.display = "none";
            dropZone.style.display = "none";

            if (task.status === "done" && task.result) {
              renderResult(task.result);
            } else if (task.status === "error") {
              showError(task.message || "未知错误");
            }
            return;
          }

          if (attempts < maxAttempts) {
            setTimeout(check, 1000);
          }
        });
    }

    check();
  }

  function renderResult(result) {
    resultSection.style.display = "block";
    document.getElementById("taskId").textContent = result.task_id;

    var overview = document.getElementById("overview");
    var diagSlots = result.diagnostic_slots || [];
    var activeSlots = diagSlots.filter(function (s) { return s.role === "active"; });
    var unknownSlots = diagSlots.filter(function (s) { return s.role === "unknown"; });
    var switchoverCount = (result.switchover_timeline || []).length;

    overview.innerHTML =
      '<div class="overview-item"><div class="value">' + escHtml(result.package_name || "未知") + '</div><div class="label">压缩包</div></div>' +
      '<div class="overview-item"><div class="value">' + diagSlots.length + '</div><div class="label">总槽位</div></div>' +
      '<div class="overview-item"><div class="value">' + activeSlots.length + '</div><div class="label">ACTIVE 主控</div></div>' +
      '<div class="overview-item"><div class="value">' + unknownSlots.length + '</div><div class="label">UNKNOWN</div></div>' +
      '<div class="overview-item"><div class="value">' + switchoverCount + '</div><div class="label">倒换事件</div></div>';

    var boardList = document.getElementById("boardList");
    boardList.innerHTML = "";

    diagSlots.forEach(function (slot) {
      var card = document.createElement("div");
      card.className = "board-card";

      var diagLogs = slot.diagnostic_logs || [];
      var activePeriods = slot.active_periods || [];

      // 主控时段
      var periodHtml = "";
      if (activePeriods.length > 0) {
        periodHtml = '<div class="log-group"><h4>主控时段 (' + activePeriods.length + ' 段)</h4><div style="font-size:13px;color:#555">';
        activePeriods.forEach(function (p) {
          periodHtml += '<div style="padding:4px 8px;margin:2px 0;background:#e8f5e9;border-radius:4px">' +
            escHtml(p.start) + ' ~ ' + escHtml(p.end) +
            ' <span style="color:#999">(' + (p.duration_seconds || 0).toFixed(0) + 's)</span></div>';
        });
        periodHtml += '</div></div>';
      }

      var header =
        '<div class="board-header">' +
        '<span class="board-name">slot_' + escHtml(slot.slot_id) + '</span>' +
        '<span class="badge badge-main_control">主控板</span>' +
        '<span class="badge badge-' + (slot.role || "unknown") + '">' + (slot.role || "unknown") + '</span>' +
        '</div>';

      var logsHtml = periodHtml + '<div class="log-group"><h4>诊断日志文件 (' + diagLogs.length + ' 个)</h4><ul class="log-list">';
      diagLogs.forEach(function (entry) {
        var dump = entry.dump_time ? '<span style="color:#888;font-size:12px">转储:' + entry.dump_time + '</span> ' : '';
        var cts = entry.content_timestamp_count ? '<span style="color:#4a6cf7;font-size:12px">[' + entry.content_timestamp_count + ' 条]</span> ' : '';
        logsHtml += '<li class="log-item">' +
          dump + cts +
          '<span class="log-name">' + escHtml(entry.name) + '</span>' +
          (entry.size_bytes ? '<span class="size">' + formatSize(entry.size_bytes) + '</span>' : '') +
          (entry.compressed ? '<span class="compressed-tag">压缩</span>' : '') +
          '</li>';
      });
      logsHtml += '</ul></div>';

      card.innerHTML = header + logsHtml;
      boardList.appendChild(card);
    });

    // 私有日志
    var privateSlots = result.private_slots || [];
    privateSlots.forEach(function (ps) {
      var card = document.createElement("div");
      card.className = "board-card";
      var cpuInfo = ps.cpu_id ? ' [CPU: ' + escHtml(ps.cpu_id) + ']' : '';
      var header = '<div class="board-header">' +
        '<span class="board-name">' + escHtml(ps.dir_name) + '</span>' +
        '<span class="badge badge-interface">varlog</span>' +
        '<span style="font-size:13px;color:#888">slot_id=' + escHtml(ps.slot_id) + cpuInfo + '</span>' +
        '</div>';
      var journalLogs = ps.journal_logs || [];
      var logsHtml = '<div class="log-group"><h4>journal 日志 (' + journalLogs.length + ' 个文件)</h4><ul class="log-list">';
      journalLogs.forEach(function (jl) {
        var seqLabel = jl.sequence === 0 ? '当前' : '历史#' + jl.sequence;
        logsHtml += '<li class="log-item">' +
          '<span style="color:#137333;font-size:11px">[' + seqLabel + ']</span> ' +
          '<span class="log-name">' + escHtml(jl.name) + '</span>' +
          (jl.size_bytes ? '<span class="size">' + formatSize(jl.size_bytes) + '</span>' : '') +
          (jl.compressed ? '<span class="compressed-tag">gzip</span>' : '') +
          '</li>';
      });
      logsHtml += '</ul></div>';
      card.innerHTML = header + logsHtml;
      boardList.appendChild(card);
    });

    // AAA 机制模块日志
    if (result.aaa_results && result.aaa_results.length > 0) {
      result.aaa_results.forEach(function (aaa) {
        var aaaCard = document.createElement("div");
        aaaCard.className = "board-card";
        var aaaHeader = '<div class="board-header">' +
          '<span class="board-name">AAA [' + escHtml(aaa.module_name) + ']</span>' +
          (aaa.active_master_slots && aaa.active_master_slots.length > 0
            ? '<span class="badge badge-active">主控确认: slot_' + aaa.active_master_slots.join(", slot_") + '</span>'
            : '') +
          '</div>';
        var aaaHtml = '';
        (aaa.slots || []).forEach(function (s) {
          var totalLogs = 0;
          var totalProcs = 0;
          (s.board_cycles || []).forEach(function (c) {
            totalProcs += (c.processes || []).length;
            (c.processes || []).forEach(function (p) { totalLogs += p.total_count || 0; });
          });
          aaaHtml += '<div class="log-group"><h4>slot_' + escHtml(s.slot_id) +
            ': ' + (s.board_cycles || []).length + ' 周期, ' + totalProcs + ' 进程, ' + totalLogs + ' 条</h4>';
          (s.board_cycles || []).forEach(function (c) {
            aaaHtml += '<div style="font-size:13px;margin:4px 0;color:#555">' + escHtml(c.dir_name) + '</div>';
            (c.processes || []).forEach(function (p) {
              var missing = p.missing_sequences && p.missing_sequences.length > 0
                ? ' <span style="color:#c5221f">丢号:' + p.missing_sequences.join(",") + '</span>' : '';
              aaaHtml += '<div style="font-size:12px;margin-left:16px">' +
                escHtml(p.process_name) + '-' + escHtml(p.pid) + ': ' + p.total_count + ' 条' + missing + '</div>';
            });
          });
          aaaHtml += '</div>';
        });
        aaaCard.innerHTML = aaaHeader + aaaHtml;
        boardList.appendChild(aaaCard);
      });
    }

    // 倒换时间线
    var timeline = result.switchover_timeline || [];
    var timelineSection = document.getElementById("timelineSection");
    var timelineEl = document.getElementById("timeline");
    if (timeline.length > 0) {
      timelineSection.style.display = "block";
      timelineEl.innerHTML = timeline.map(function (e) {
        return '<div class="timeline-event"><strong>slot_' + escHtml(e.from_slot) + '</strong> → <strong>slot_' + escHtml(e.to_slot) + '</strong>' +
          (e.time ? ' <span style="color:#888">(' + e.time + ')</span>' : '') +
          '<br><small>' + escHtml(e.evidence || "") + '</small></div>';
      }).join("");
    } else {
      timelineSection.style.display = "none";
    }

    // 错误
    if (result.errors && result.errors.length > 0) {
      var errSection = document.getElementById("errorSection");
      errSection.style.display = "block";
      errSection.textContent = result.errors.join("\n");
    }
  }

  function showError(msg) {
    resultSection.style.display = "block";
    document.getElementById("errorSection").style.display = "block";
    document.getElementById("errorSection").textContent = msg;
    uploadStatus.style.display = "none";
    dropZone.style.display = "block";
  }

  function escHtml(s) {
    if (!s) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }
})();
