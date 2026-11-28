#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <ImageIO/ImageIO.h>
int main(int argc, const char **argv){ @autoreleasepool {for(int i=1;i<argc;i++) {@autoreleasepool{
 NSURL *url=[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[i]]];
 CGImageSourceRef src=CGImageSourceCreateWithURL((__bridge CFURLRef)url,NULL);
 if(!src) return 2; CGImageRef img=CGImageSourceCreateImageAtIndex(src,0,NULL);CFRelease(src); if(!img)return 3;
 VNDetectFaceLandmarksRequest *req=[VNDetectFaceLandmarksRequest new];
 VNImageRequestHandler *h=[[VNImageRequestHandler alloc]initWithCGImage:img options:@{}]; NSError *err=nil;
 BOOL ok=[h performRequests:@[req] error:&err];CGImageRelease(img);
 printf("%d %d %ld",i,(int)ok,(long)err.code);
 for(VNFaceObservation *f in req.results){CGRect r=f.boundingBox; VNFaceLandmarkRegion2D *lip=f.landmarks.innerLips;
 double ymin=1,ymax=0,xmin=1,xmax=0;for(int q=0;q<lip.pointCount;q++){CGPoint p=lip.normalizedPoints[q]; ymin=fmin(ymin,p.y);ymax=fmax(ymax,p.y);xmin=fmin(xmin,p.x);xmax=fmax(xmax,p.x);}
 printf(" %.3f,%.3f,%.3f,%.3f,%.3f,%.3f",CGRectGetMidX(r),1-CGRectGetMidY(r),r.size.width,r.size.height,f.confidence,(xmax>xmin?(ymax-ymin)*r.size.height/((xmax-xmin)*r.size.width):0));}
 puts(""); } } return 0; } }
