/** Storage keys for reader preferences, shared by the inline script and the client store. */
export const THEME_KEY = "mercalli:theme";
export const ZONE_KEY = "mercalli:zone";

/** Applies the saved theme before first paint, so a dark reader never sees a light flash. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}})()`;
