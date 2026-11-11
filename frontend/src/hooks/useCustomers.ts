import { useEffect, useState } from "react";
import { api } from "../api";
import type { Customer } from "../types";

/** All customers. Optional data, so a failed load just leaves the list empty and sets `failed`. */
export function useCustomers(): { customers: Customer[]; failed: boolean } {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    api
      .listCustomers()
      .then((list) => active && setCustomers(list))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, []);
  return { customers, failed };
}
