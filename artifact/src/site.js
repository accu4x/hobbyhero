// Site edition only: the service worker makes the page installable and lets it start offline.
// A new deploy is picked up in the background and shown on the next visit.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
