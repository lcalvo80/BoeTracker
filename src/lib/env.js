// Lee vars tanto en Vite (VITE_) como CRA (REACT_APP_)
export const ENV = {
  BACKEND_URL:
    (typeof import.meta !== "undefined" && import.meta.env?.VITE_BACKEND_URL) ||
    process.env.REACT_APP_BACKEND_URL ||
    "http://localhost:8000",
  PRICE_PRO_MONTHLY_ID:
    (typeof import.meta !== "undefined" && import.meta.env?.VITE_PRICE_PRO_MONTHLY_ID) ||
    process.env.REACT_APP_PRICE_PRO_MONTHLY_ID ||
    "",
  PRICE_ENTERPRISE_SEAT_ID:
    (typeof import.meta !== "undefined" && import.meta.env?.VITE_PRICE_ENTERPRISE_SEAT_ID) ||
    process.env.REACT_APP_PRICE_ENTERPRISE_SEAT_ID ||
    "",
};
