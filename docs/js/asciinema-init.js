// Mounts an asciinema player on each <div class="asciicast" data-src="..." data-start-at="...">.
// Players pause at the next marker in the recording, so one cast can back several sections.
// data-poster-at picks the preview frame when the start frame is blank.
function mountAsciicasts() {
  document.querySelectorAll(".asciicast[data-src]:not([data-mounted])").forEach((el) => {
    const startAt = parseFloat(el.dataset.startAt || "0");
    AsciinemaPlayer.create(el.dataset.src, el, {
      startAt,
      speed: parseFloat(el.dataset.speed || "1.5"),
      pauseOnMarkers: true,
      preload: true,
      poster: `npt:${el.dataset.posterAt || startAt}`,
      fit: "width",
    });
    el.dataset.mounted = "true";
  });
}

if (window.document$) {
  document$.subscribe(mountAsciicasts);
} else {
  document.addEventListener("DOMContentLoaded", mountAsciicasts);
}
