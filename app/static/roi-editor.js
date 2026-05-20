(() => {
  const canvas = document.getElementById("roi-canvas");
  const imageInput = document.getElementById("reference-image");
  const roiNameInput = document.getElementById("roi-name");
  const roiList = document.getElementById("roi-list");
  const deleteButton = document.getElementById("delete-roi");
  const clearButton = document.getElementById("clear-rois");
  const roisJsonInput = document.getElementById("rois-json");
  const form = document.getElementById("setup-form");

  if (!canvas || !form) {
    return;
  }

  const ctx = canvas.getContext("2d");
  const HANDLE_RADIUS = 7;
  const state = {
    image: null,
    rois: [],
    activeIndex: -1,
    drawStart: null,
    draftRect: null,
    draggingHandle: null,
  };

  function clampPoint(point) {
    return {
      x: Math.max(0, Math.min(canvas.width, Math.round(point.x))),
      y: Math.max(0, Math.min(canvas.height, Math.round(point.y))),
    };
  }

  function boundsFromPoints(points) {
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    return {
      x: Math.min(...xs),
      y: Math.min(...ys),
      width: Math.max(...xs) - Math.min(...xs),
      height: Math.max(...ys) - Math.min(...ys),
    };
  }

  function rectangleToPoints(rect) {
    return [
      { x: rect.x, y: rect.y },
      { x: rect.x + rect.width, y: rect.y },
      { x: rect.x + rect.width, y: rect.y + rect.height },
      { x: rect.x, y: rect.y + rect.height },
    ];
  }

  function normalizePoints(points) {
    if (!points || points.length !== 4) {
      return points || [];
    }

    return rectangleToPoints(boundsFromPoints(points));
  }

  function normalizeRect(start, end) {
    const x = Math.min(start.x, end.x);
    const y = Math.min(start.y, end.y);
    const width = Math.abs(start.x - end.x);
    const height = Math.abs(start.y - end.y);
    return { x, y, width, height };
  }

  function pointDistance(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function diagonalHandles(roi) {
    const bounds = boundsFromPoints(roi.points);
    return [
      { name: "topLeft", point: { x: bounds.x, y: bounds.y } },
      { name: "bottomRight", point: { x: bounds.x + bounds.width, y: bounds.y + bounds.height } },
    ];
  }

  function drawPolygon(points) {
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let index = 1; index < points.length; index += 1) {
      ctx.lineTo(points[index].x, points[index].y);
    }
    ctx.closePath();
  }

  function pointInPolygon(point, points) {
    let inside = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
      const xi = points[i].x;
      const yi = points[i].y;
      const xj = points[j].x;
      const yj = points[j].y;
      const intersect = ((yi > point.y) !== (yj > point.y))
        && (point.x < ((xj - xi) * (point.y - yi)) / ((yj - yi) || 1) + xi);
      if (intersect) {
        inside = !inside;
      }
    }
    return inside;
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (state.image) {
      ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
    } else {
      ctx.fillStyle = "#f9fafb";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    state.rois.forEach((roi, index) => {
      const active = index === state.activeIndex;
      drawPolygon(roi.points);
      ctx.fillStyle = active ? "rgba(15, 23, 42, 0.14)" : "rgba(37, 99, 235, 0.14)";
      ctx.fill();
      ctx.strokeStyle = active ? "#111827" : "#2563eb";
      ctx.lineWidth = 2;
      ctx.stroke();

      const labelAnchor = boundsFromPoints(roi.points);
      ctx.fillStyle = "#111827";
      ctx.font = "14px Segoe UI";
      ctx.fillText(roi.name, labelAnchor.x + 4, Math.max(16, labelAnchor.y - 8));

      if (active) {
        diagonalHandles(roi).forEach((handle) => {
          ctx.beginPath();
          ctx.arc(handle.point.x, handle.point.y, HANDLE_RADIUS, 0, Math.PI * 2);
          ctx.fillStyle = "#ffffff";
          ctx.fill();
          ctx.strokeStyle = "#111827";
          ctx.lineWidth = 2;
          ctx.stroke();
        });
      }
    });

    if (state.draftRect) {
      const draftPoints = rectangleToPoints(state.draftRect);
      drawPolygon(draftPoints);
      ctx.strokeStyle = "#dc2626";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function renderRoiList() {
    roiList.innerHTML = "";
    state.rois.forEach((roi, index) => {
      const bounds = boundsFromPoints(roi.points);
      const item = document.createElement("li");
      item.className = `roi-item ${index === state.activeIndex ? "active" : ""}`;
      item.innerHTML = `
        <strong>${roi.name}</strong>
        <span>x=${bounds.x}, y=${bounds.y}, w=${bounds.width}, h=${bounds.height}</span>
        <span>TL=(${bounds.x}, ${bounds.y}) BR=(${bounds.x + bounds.width}, ${bounds.y + bounds.height})</span>
      `;
      item.addEventListener("click", () => {
        state.activeIndex = index;
        renderRoiList();
        draw();
      });
      roiList.appendChild(item);
    });
  }

  function setImageSource(source) {
    if (!source) {
      return;
    }
    const image = new Image();
    image.onload = () => {
      state.image = image;
      canvas.width = image.width;
      canvas.height = image.height;
      draw();
    };
    image.src = source;
  }

  function canvasPoint(event) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return clampPoint({
      x: (event.clientX - rect.left) * scaleX,
      y: (event.clientY - rect.top) * scaleY,
    });
  }

  function findHandle(point) {
    for (let roiIndex = 0; roiIndex < state.rois.length; roiIndex += 1) {
      const roi = state.rois[roiIndex];
      const handles = diagonalHandles(roi);
      for (let handleIndex = 0; handleIndex < handles.length; handleIndex += 1) {
        if (pointDistance(point, handles[handleIndex].point) <= HANDLE_RADIUS + 2) {
          return { roiIndex, handleName: handles[handleIndex].name };
        }
      }
    }
    return null;
  }

  function findPolygon(point) {
    for (let index = state.rois.length - 1; index >= 0; index -= 1) {
      if (pointInPolygon(point, state.rois[index].points)) {
        return index;
      }
    }
    return -1;
  }

  canvas.addEventListener("mousedown", (event) => {
    if (!state.image) {
      return;
    }

    const point = canvasPoint(event);
    const handle = findHandle(point);
    if (handle) {
      state.activeIndex = handle.roiIndex;
      state.draggingHandle = handle;
      renderRoiList();
      draw();
      return;
    }

    const polygonIndex = findPolygon(point);
    if (polygonIndex >= 0) {
      state.activeIndex = polygonIndex;
      renderRoiList();
      draw();
      return;
    }

    if (!roiNameInput.value.trim()) {
      window.alert("Enter an ROI name before creating a new ROI.");
      return;
    }

    state.drawStart = point;
    state.draftRect = null;
  });

  canvas.addEventListener("mousemove", (event) => {
    const point = canvasPoint(event);

    if (state.draggingHandle) {
      const roi = state.rois[state.draggingHandle.roiIndex];
      const bounds = boundsFromPoints(roi.points);
      const topLeft = { x: bounds.x, y: bounds.y };
      const bottomRight = { x: bounds.x + bounds.width, y: bounds.y + bounds.height };
      const fixedPoint = state.draggingHandle.handleName === "topLeft" ? bottomRight : topLeft;
      roi.points = rectangleToPoints(normalizeRect(fixedPoint, point));
      renderRoiList();
      draw();
      return;
    }

    if (!state.drawStart) {
      return;
    }

    state.draftRect = normalizeRect(state.drawStart, point);
    draw();
  });

  canvas.addEventListener("mouseup", (event) => {
    if (state.draggingHandle) {
      const roi = state.rois[state.draggingHandle.roiIndex];
      roi.points = normalizePoints(roi.points);
      state.draggingHandle = null;
      renderRoiList();
      draw();
      return;
    }

    if (!state.drawStart) {
      return;
    }

    const rect = normalizeRect(state.drawStart, canvasPoint(event));
    state.drawStart = null;
    state.draftRect = null;
    if (rect.width < 8 || rect.height < 8) {
      draw();
      return;
    }

    const name = roiNameInput.value.trim();
    if (state.rois.some((roi) => roi.name === name)) {
      window.alert("ROI names must be unique within the selected CCTV.");
      draw();
      return;
    }

    state.rois.push({
      name,
      points: normalizePoints(rectangleToPoints(rect)),
    });
    state.activeIndex = state.rois.length - 1;
    renderRoiList();
    draw();
  });

  canvas.addEventListener("mouseleave", () => {
    if (state.draggingHandle) {
      const roi = state.rois[state.draggingHandle.roiIndex];
      roi.points = normalizePoints(roi.points);
      state.draggingHandle = null;
      renderRoiList();
      draw();
    }
  });

  imageInput.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = (loadEvent) => setImageSource(loadEvent.target.result);
    reader.readAsDataURL(file);
  });

  deleteButton.addEventListener("click", () => {
    if (state.activeIndex < 0) {
      return;
    }
    state.rois.splice(state.activeIndex, 1);
    state.activeIndex = Math.min(state.activeIndex, state.rois.length - 1);
    renderRoiList();
    draw();
  });

  clearButton.addEventListener("click", () => {
    state.rois = [];
    state.activeIndex = -1;
    renderRoiList();
    draw();
  });

  form.addEventListener("submit", (event) => {
    if (!state.rois.length) {
      event.preventDefault();
      window.alert("Add at least one ROI before saving the CCTV setup.");
      return;
    }
    const roiNames = state.rois.map((roi) => roi.name);
    if (roiNames.length !== new Set(roiNames).size) {
      event.preventDefault();
      window.alert("ROI names must be unique.");
      return;
    }
    state.rois = state.rois.map((roi) => ({
      ...roi,
      points: normalizePoints(roi.points),
    }));
    renderRoiList();
    draw();
    roisJsonInput.value = JSON.stringify(state.rois);
  });

  const initial = window.initialConfig;
  if (initial) {
    state.rois = (initial.areas || []).map((roi) => ({
      name: roi.name,
      points: normalizePoints((roi.points || []).map((point) => ({ x: point.x, y: point.y }))),
    }));
    if (state.rois.length > 0) {
      state.activeIndex = 0;
    }
    renderRoiList();
    if (canvas.dataset.referenceUrl) {
      setImageSource(canvas.dataset.referenceUrl);
    }
  } else {
    canvas.width = 960;
    canvas.height = 540;
    draw();
  }
})();
