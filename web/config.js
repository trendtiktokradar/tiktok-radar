// Configuración del panel.
// En local (localhost) lee el data.json de esta misma carpeta.
// Publicado (Vercel) lee el data.json que el programa sube cada 5 min a la rama "data" de GitHub,
// así la web NO se redespliega en cada actualización (Vercel solo despliega cuando cambia el código).
(function () {
  var GITHUB_USER = "trendtiktokradar";
  var GITHUB_REPO = "tiktok-radar";
  var local = /^(localhost|127\.0\.0\.1|0\.0\.0\.0)$/.test(location.hostname) || location.protocol === "file:";
  var remote = "https://raw.githubusercontent.com/" + GITHUB_USER + "/" + GITHUB_REPO + "/data/data.json";
  window.RADAR_CONFIG = {
    dataUrl: local || GITHUB_USER.indexOf("__") === 0 ? "data.json" : remote,
    fallbackUrl: "data.json",   // copia incluida en el despliegue por si GitHub falla
    refreshSeconds: 120
  };
})();
