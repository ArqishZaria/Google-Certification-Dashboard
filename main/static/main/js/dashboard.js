(function () {
  const el = document.getElementById("chart-data");
  if (!el || typeof Chart === "undefined") return;
  const D = JSON.parse(el.textContent);
  const P = ["#1f6feb", "#1f9d6b", "#f0a500", "#d64545", "#7a5af8", "#0ea5b7", "#e56bb0", "#8a99ad", "#4b5a70"];
  const STAGE = {started: "#1f9d6b", not_started: "#f0a500", pending: "#b7c2d3"};
  const BANDS = ["#d64545", "#f0a500", "#e6c200", "#5aa0f0", "#1f9d6b", "#7a5af8", "#b7c2d3"];
  Chart.defaults.font.family = '"Segoe UI",system-ui,sans-serif';
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  const mk = (id, cfg) => { const c = document.getElementById(id); if (c) new Chart(c, cfg); };
  const opts = (extra) => Object.assign({responsive: true, maintainAspectRatio: false}, extra || {});
  const stacked = {scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}}};
  const stackedH = {indexAxis: "y", scales: {x: {stacked: true, beginAtZero: true}, y: {stacked: true}}};
  const stageSets = (s) => [
    {label: "Started", data: s.started, backgroundColor: STAGE.started},
    {label: "Accepted - not started", data: s.not_started, backgroundColor: STAGE.not_started},
    {label: "Pending acceptance", data: s.pending, backgroundColor: STAGE.pending}];

  mk("c_funnel", {type: "bar", data: {labels: D.funnel.labels, datasets: [{data: D.funnel.values, backgroundColor: P[0]}]},
    options: opts({indexAxis: "y", plugins: {legend: {display: false}}})});
  mk("c_status", {type: "doughnut", data: {labels: D.status.labels, datasets: [{data: D.status.values, backgroundColor: ["#1f9d6b", "#1f6feb", "#7a5af8", "#f0a500", "#b7c2d3", "#d64545", "#8a99ad"]}]},
    options: opts({plugins: {legend: {position: "right"}}})});
  mk("c_groups_lic", {type: "bar", data: {labels: D.groups.labels, datasets: [
      {label: "Licences issued", data: D.groups.licensed, backgroundColor: "#b7c2d3"},
      {label: "Accepted", data: D.groups.accepted, backgroundColor: P[0]},
      {label: "Started learning", data: D.groups.started, backgroundColor: P[1]}]}, options: opts({scales: {y: {beginAtZero: true}}})});
  mk("c_groups_notstarted", {type: "bar", data: {labels: D.groups.labels, datasets: [
      {label: "Accepted - not started", data: D.groups.not_started, backgroundColor: P[2]},
      {label: "Pending acceptance", data: D.groups.pending, backgroundColor: "#b7c2d3"}]}, options: opts(stacked)});
  mk("c_groups_rate", {type: "bar", data: {labels: D.groups.labels, datasets: [
      {label: "Completion % (of accepted)", data: D.groups.completion_rate, backgroundColor: P[1]},
      {label: "Avg PC progress %", data: D.groups.avg_progress, backgroundColor: P[0]}]}, options: opts({scales: {y: {beginAtZero: true}}})});
  const gb = D.group_bands;
  mk("c_group_bands", {type: "bar", data: {labels: gb.groups, datasets: gb.bands.map((b, i) => ({label: b, data: gb.series[b], backgroundColor: BANDS[i % BANDS.length]}))}, options: opts(stacked)});
  mk("c_progress", {type: "bar", data: {labels: D.progress_dist.labels, datasets: [{data: D.progress_dist.values, backgroundColor: ["#d64545", "#f0a500", "#5aa0f0", "#7a5af8", "#1f9d6b"]}]}, options: opts({plugins: {legend: {display: false}}})});
  mk("c_pop", {type: "bar", data: {labels: D.popularity.labels, datasets: [
      {label: "Completed", data: D.popularity.completed, backgroundColor: P[1]},
      {label: "Not completed", data: D.popularity.incomplete, backgroundColor: "#b7c2d3"}]}, options: opts(stackedH)});
  const cb = D.course_bands;
  const CB = ["#8a99ad", "#d64545", "#e56bb0", "#f0a500", "#e6c200", "#5aa0f0", "#0ea5b7", "#7a5af8", "#1f9d6b"];
  mk("c_course_bands", {type: "bar", data: {labels: cb.labels, datasets: cb.bands.map((b, i) => ({label: b, data: cb.series[b], backgroundColor: CB[i]}))}, options: opts(stackedH)});
  mk("c_cpl", {type: "bar", data: {labels: D.cpl.labels, datasets: [{label: "Participants", data: D.cpl.values, backgroundColor: P[4]}]}, options: opts({plugins: {legend: {display: false}}, scales: {x: {title: {display: true, text: "Specializations enrolled per learner (0 = individual courses only)"}}}})});
  for (const k of ["gender", "prof", "income", "age"]) {
    const s = D[k];
    mk("c_" + k, {type: "bar", data: {labels: s.labels, datasets: stageSets(s)}, options: opts(k === "income" ? stackedH : stacked)});
  }
  const e = D.edu_by_prof;
  mk("c_edu", {type: "bar", data: {labels: e.rows, datasets: e.cols.map((c, j) => ({label: c, data: e.cells.map(r => r[j]), backgroundColor: P[j % P.length]}))}, options: opts(stacked)});
  mk("c_weekly", {type: "line", data: {labels: D.weekly.weeks, datasets: [
      {label: "Accepted", data: D.weekly.accepted, borderColor: P[0], tension: .25},
      {label: "Started", data: D.weekly.started, borderColor: P[1], tension: .25},
      {label: "Completed PC", data: D.weekly.completed, borderColor: P[3], tension: .25}]}, options: opts({scales: {y: {beginAtZero: true}}})});
  mk("c_monthly", {type: "bar", data: {labels: D.monthly.labels, datasets: [
      {label: "Licences issued", data: D.monthly.licensed, backgroundColor: "#b7c2d3"},
      {label: "Accepted", data: D.monthly.accepted, backgroundColor: P[0]},
      {label: "Started", data: D.monthly.started, backgroundColor: P[1]},
      {label: "Completed PC", data: D.monthly.completed, backgroundColor: P[3]}]}, options: opts({scales: {y: {beginAtZero: true}}})});
})();
