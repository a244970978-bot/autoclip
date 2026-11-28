// Offline single-subject framing. Outputs normalized coordinates, never a frame or identity.
#import <Foundation/Foundation.h>
#import <AVFoundation/AVFoundation.h>
#import <Vision/Vision.h>
int main(int argc,const char **argv) { @autoreleasepool {
    if (argc!=4) return 2;
    double a=atof(argv[2]), b=atof(argv[3]); if (!(b>a)) return 2;
    NSURL *url=[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
    AVAssetImageGenerator *gen=[[AVAssetImageGenerator alloc] initWithAsset:[AVURLAsset URLAssetWithURL:url options:nil]];
    gen.appliesPreferredTrackTransform=YES; gen.maximumSize=CGSizeMake(640,640);
    gen.requestedTimeToleranceAfter=gen.requestedTimeToleranceBefore=CMTimeMakeWithSeconds(.1,600);
    double px=0,py=0;
    for (int i=0;i<3;i++) {
        CGImageRef image=[gen copyCGImageAtTime:CMTimeMakeWithSeconds(a+(b-a)*(.15+.35*i),600) actualTime:nil error:nil];
        if (!image) return 0;
        VNDetectFaceRectanglesRequest *req=[VNDetectFaceRectanglesRequest new]; req.usesCPUOnly=YES;
        VNImageRequestHandler *handler=[[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
        BOOL ok=[handler performRequests:@[req] error:nil]; CGImageRelease(image);
        VNFaceObservation *face=req.results.firstObject;
        if (!ok || req.results.count!=1 || face.confidence<.8 || face.boundingBox.size.width<.13) return 0;
        double x=CGRectGetMidX(face.boundingBox), y=1-CGRectGetMidY(face.boundingBox);
        if (i && hypot(x-px,y-py)>.075) return 0;
        px=x;py=y;
    }
    printf("%.4f %.4f\n",px,py);return 0;
} }
