window.SUPPLY_CHAIN_TRANSPORT_HISTORY = {
  capturedAt: "2026-08-09T23:25:40+08:00",
  source: "seaya_oms_inbound_managein_completed_batches_logged_in_readonly",
  preparationDays: 3,
  domesticWarehouseDays: 4,
  minimumSamples: 5,
  percentile: 0.8,
  method: "max(baseline, nearest-rank P80(created-to-sign days) - preparation - domestic-warehouse)",
  completedRows: 21,
  excludedRows: 4,
  exclusionPolicy: "exclude Seaya rows marked abnormal and rows without a named first-mile carrier",
  regions: {
    MY: {
      baselineTransportDays: 25,
      eligibleSamples: 2,
      observedTotalDays: [24, 33],
      p80TotalDays: 33,
      derivedTransportDays: 26,
      effectiveTransportDays: 25,
      state: "FALLBACK_INSUFFICIENT_SAMPLE"
    },
    TH: {
      baselineTransportDays: 15,
      eligibleSamples: 9,
      observedTotalDays: [12, 15, 19, 22, 24, 24, 24, 27, 30],
      p80TotalDays: 27,
      derivedTransportDays: 20,
      effectiveTransportDays: 20,
      state: "HISTORICAL_P80_UPLIFT"
    },
    VN: {
      baselineTransportDays: 15,
      eligibleSamples: 5,
      observedTotalDays: [8, 8, 9, 12, 21],
      p80TotalDays: 12,
      derivedTransportDays: 5,
      effectiveTransportDays: 15,
      state: "BASELINE_FLOOR"
    },
    PH: {
      baselineTransportDays: 25,
      eligibleSamples: 1,
      observedTotalDays: [30],
      p80TotalDays: 30,
      derivedTransportDays: 23,
      effectiveTransportDays: 25,
      state: "FALLBACK_INSUFFICIENT_SAMPLE"
    }
  }
};
