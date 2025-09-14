import React, { useState } from "react";
import { useAuth } from "@clerk/clerk-react";
import { api } from "../../lib/api";
import { ENV } from "../../lib/env";

export function BuyProButton() {
  const { getToken } = useAuth();
  const [loading, setLoading] = useState(false);
  const priceId = ENV.PRICE_PRO_MONTHLY_ID;

  const onClick = async () => {
    try {
      setLoading(true);
      const token = await getToken({ template: "backend" }).catch(() => getToken());
      const { checkout_url } = await api("/billing/checkout", {
        token,
        method: "POST",
        body: { price_id: priceId, is_org: false },
      });
      window.location.assign(checkout_url);
    } catch (e) {
      alert(`Error creando checkout: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button onClick={onClick} disabled={loading || !priceId} className="px-4 py-2 rounded bg-blue-600 text-white">
      {loading ? "Redirigiendo..." : "Pasar a Pro"}
    </button>
  );
}

export function EnterpriseCheckoutButton({ seats = 5 }) {
  const { getToken } = useAuth();
  const [loading, setLoading] = useState(false);
  const priceId = ENV.PRICE_ENTERPRISE_SEAT_ID;

  const onClick = async () => {
    try {
      setLoading(true);
      const token = await getToken({ template: "backend" }).catch(() => getToken());
      const { checkout_url } = await api("/billing/checkout", {
        token,
        method: "POST",
        body: { price_id: priceId, is_org: true, quantity: seats },
      });
      window.location.assign(checkout_url);
    } catch (e) {
      alert(`Error creando checkout Enterprise: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button onClick={onClick} disabled={loading || !priceId} className="px-4 py-2 rounded bg-indigo-600 text-white">
      {loading ? "Redirigiendo..." : `Comprar Enterprise (${seats} seats)`}
    </button>
  );
}

export function BillingPortalButton() {
  const { getToken } = useAuth();
  const [loading, setLoading] = useState(false);

  const onClick = async () => {
    try {
      setLoading(true);
      const token = await getToken({ template: "backend" }).catch(() => getToken());
      const { portal_url } = await api("/billing/portal", { token, method: "POST" });
      window.location.assign(portal_url);
    } catch (e) {
      alert(`Error abriendo portal: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button onClick={onClick} disabled={loading} className="px-4 py-2 rounded border">
      {loading ? "Abriendo..." : "Gestionar facturación"}
    </button>
  );
}
