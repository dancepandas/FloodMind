import { Worker, Viewer } from "@react-pdf-viewer/core";
import { defaultLayoutPlugin } from "@react-pdf-viewer/default-layout";
import "@react-pdf-viewer/core/lib/styles/index.css";
import "@react-pdf-viewer/default-layout/lib/styles/index.css";

interface PdfPreviewProps {
  url: string;
}

export function PdfPreview({ url }: PdfPreviewProps) {
  const defaultLayoutPluginInstance = defaultLayoutPlugin({
    sidebarTabs: () => [],
  });

  return (
    <Worker workerUrl="/pdf.worker.min.js">
      <div className="h-full">
        <Viewer
          fileUrl={url}
          plugins={[defaultLayoutPluginInstance]}
          defaultScale={1.2}
          renderError={() => (
            <div className="flex items-center justify-center h-full text-destructive text-sm">
              PDF 加载失败，请尝试下载后查看
            </div>
          )}
        />
      </div>
    </Worker>
  );
}
