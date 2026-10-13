(function(root) {
  "use strict";
  const themes = ["minimal", "engineering", "operations"];
  function validate(value) {
    if (!value || value.version !== 1 || !themes.includes(value.theme) || !["compact", "cozy"].includes(value.density) || !["full", "off"].includes(value.animation) || typeof value.autoRefresh !== "boolean") throw new Error("不支持的视图档案，请使用 REVOL 导出的 v1 JSON 文件。");
    return {version: 1, name: value.theme, theme: value.theme, density: value.density, animation: value.animation, autoRefresh: value.autoRefresh};
  }
  if (typeof module === "object" && module.exports) module.exports = {validate};
  else root.BmsProfiles = {validate};
})(typeof window === "undefined" ? globalThis : window);
