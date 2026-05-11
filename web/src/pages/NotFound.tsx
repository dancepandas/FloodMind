import { useLocation } from "react-router-dom";
import { useEffect } from "react";
import { ArrowLeft } from "lucide-react";

const NotFound = () => {
  const location = useLocation();

  useEffect(() => {
    console.error("404 Error: User attempted to access non-existent route:", location.pathname);
  }, [location.pathname]);

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-background">
      <div className="text-center flex flex-col items-center gap-5">
        <div className="text-[80px] font-bold text-foreground/10 leading-none tracking-tighter select-none">
          404
        </div>
        <div>
          <p className="text-lg font-medium text-foreground/80">页面未找到</p>
          <p className="text-sm text-muted-foreground/60 mt-1">请求的路径不存在</p>
        </div>
        <a
          href="/"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-primary/8 text-primary hover:bg-primary hover:text-primary-foreground transition-all duration-200"
        >
          <ArrowLeft size={15} />
          返回首页
        </a>
      </div>
    </div>
  );
};

export default NotFound;
