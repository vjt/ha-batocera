// Batocera card: the box's games, full page. Served by the integration (see
// dashboard.py) and dropped into the Batocera dashboard.
class BatoceraCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entities || !config.entities.player) {
      throw new Error("batocera-card: entities.player required");
    }
    this._config = config;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.textContent = this._config.name || "Batocera";
  }

  getCardSize() {
    return 12;
  }
}

const define = () => {
  if (!customElements.get("batocera-card")) customElements.define("batocera-card", BatoceraCard);
};
define();
customElements.whenDefined("home-assistant").then(define);
