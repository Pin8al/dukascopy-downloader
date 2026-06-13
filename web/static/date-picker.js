/** Native date input wrapper — type="date" with min/max bounds. */
class DatePicker {
  constructor(container, { value = "", min = null, max = null, onChange = null } = {}) {
    this.container = container;
    this.min = min;
    this.max = max;
    this.onChange = onChange;
    this.value = "";
    this._build();
    if (value) this.setValue(value);
  }

  _build() {
    this.container.classList.add("date-picker");
    this.container.innerHTML = `<input type="date" class="date-picker-input">`;
    this.input = this.container.querySelector(".date-picker-input");
    this.input.setAttribute("lang", "en-CA");
    this._applyBounds();
    this.input.addEventListener("change", () => {
      this.value = this.input.value;
      if (this.onChange) this.onChange(this.value);
    });
  }

  _applyBounds() {
    if (this.min) this.input.min = this.min;
    else this.input.removeAttribute("min");
    if (this.max) this.input.max = this.max;
    else this.input.removeAttribute("max");
  }

  _clamp(iso) {
    if (this.min && iso < this.min) return this.min;
    if (this.max && iso > this.max) return this.max;
    return iso;
  }

  setValue(iso) {
    if (!iso) return;
    const clamped = this._clamp(iso);
    const changed = clamped !== this.value;
    this.value = clamped;
    this.input.value = clamped;
    this.container.classList.toggle("has-value", Boolean(clamped));
    if (changed && this.onChange) this.onChange(clamped);
  }

  getValue() {
    const raw = this.input.value;
    if (!raw) return "";
    return this._clamp(raw);
  }

  setMinMax(min, max) {
    this.min = min;
    this.max = max;
    this._applyBounds();
    if (this.value) {
      const clamped = this._clamp(this.value);
      if (clamped !== this.value) this.setValue(clamped);
    } else if (this.input.value) {
      const clamped = this._clamp(this.input.value);
      if (clamped !== this.input.value) this.setValue(clamped);
    }
  }
}

window.DatePicker = DatePicker;
